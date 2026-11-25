"""Coordinated backups of the database and the attachment vault (spec 8.4, R-167).

A backup is a SET folder ``reframe-YYYYMMDD-HHMMSS[-n]\\`` inside the backup folder::

    reframe.sqlite        SQLite online-backup copy (written to a local temp file, journal_mode=DELETE, moved in)
    vault-manifest.json   every SHA-256 referenced by file_version rows IN THAT DATABASE COPY (with sizes)
    COMPLETE              written last: verification results. A set without it is incomplete and never offered.

Vault content lives once in a shared, content-addressed pool next to the sets::

    vault-objects\\objects\\ab\\cdef....zlib   (same blob format as the live vault, see vault/store.py)

Each new set copies (verified) only the blobs the pool does not already hold, so sets are incremental without
chains between them; a set is restorable as long as the pool holds the blobs its manifest names. Retention deletes
set folders and then only pool blobs that no remaining set's manifest references. (A single set folder copied
elsewhere is therefore not self-contained: copy the pool with it.)

Old single-file ``reframe-*.sqlite`` backups in the folder are still listed and restorable (no vault half).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ..db.connection import connect
from ..vault.store import BlobCorrupt, BlobMissing, VaultStore, default_vault_root

_NAME_RE = re.compile(r"^reframe-(\d{8})-(\d{6})(?:-(\d+))?(\.sqlite)?$")
DB_NAME = "reframe.sqlite"
MANIFEST = "vault-manifest.json"
MARKER = "COMPLETE"
POOL_DIR = "vault-objects"
FORMAT = 1
STALE_INCOMPLETE_S = 3600


class BackupError(OSError):
    """A backup or restore could not be completed; the message names the set, files or versions involved.

    Subclasses OSError so existing callers that report ``OSError`` as a backup failure keep working."""


def default_backup_dir() -> Path:
    env = os.environ.get("REFRAME_BACKUP_DIR")
    return Path(env) if env else Path.home() / "Documents" / "Reframe Backups"


# ------------------------------------------------------------------ listing

def _is_set_dir(p: Path) -> bool:
    m = _NAME_RE.match(p.name)
    return bool(m) and not m[4] and p.is_dir()


def _is_legacy(p: Path) -> bool:
    m = _NAME_RE.match(p.name)
    return bool(m) and bool(m[4]) and p.is_file()


def is_complete(p: Path) -> bool:
    return _is_set_dir(p) and (p / MARKER).is_file() and (p / DB_NAME).is_file()


def _sort_key(p: Path):
    m = _NAME_RE.match(p.name)
    return (m[1], m[2], int(m[3] or 0), p.name)


def list_backups(folder: Path) -> list[Path]:
    """Restorable backups, newest first: complete set folders plus old single-file backups."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = [p for p in folder.iterdir() if is_complete(p) or _is_legacy(p)]
    return sorted(found, key=_sort_key, reverse=True)


def list_incomplete(folder: Path) -> list[Path]:
    """Set folders without a COMPLETE marker (interrupted or failed). Labelled, never offered for restore."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if _is_set_dir(p) and not is_complete(p)), key=_sort_key, reverse=True)


def _backup_time(p: Path) -> dt.datetime:
    m = _NAME_RE.match(p.name)
    return dt.datetime.strptime(m[1] + m[2], "%Y%m%d%H%M%S")


def newest_age_seconds(folder: Path) -> float | None:
    backups = list_backups(folder)
    if not backups:
        return None
    return (dt.datetime.now() - _backup_time(backups[0])).total_seconds()


def backup_size(p: Path) -> int:
    """Bytes of one backup: the set folder's own files (database, manifest, marker), or the legacy file.
    Shared pool blobs are reported by ``pool_size(folder)``."""
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.iterdir() if f.is_file())


def backup_db_file(p: Path) -> Path:
    return p / DB_NAME if p.is_dir() else p


def backup_info(p: Path) -> dict[str, Any]:
    """Marker contents of a set (verification results), or a minimal record for a legacy file."""
    if p.is_dir():
        try:
            return json.loads((p / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"complete": False}
    return {"complete": True, "legacy": True, "vault_blobs": 0}


def pool(folder: Path) -> VaultStore:
    return VaultStore(Path(folder) / POOL_DIR)


def pool_size(folder: Path) -> int:
    return pool(folder).size_info()["stored_bytes"]


# ------------------------------------------------------------------ create

def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _db_blobs(conn: sqlite3.Connection) -> dict[str, int]:
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT sha256, max(size) FROM file_version GROUP BY sha256")}
    except sqlite3.OperationalError:        # a database without the vault tables
        return {}


def _new_set_dir(folder: Path, now: dt.datetime) -> Path:
    base = f"reframe-{now:%Y%m%d-%H%M%S}"
    n = 0
    while True:
        name = base if n == 0 else f"{base}-{n}"
        if not (folder / name).exists() and not (folder / f"{name}.sqlite").exists():
            try:
                (folder / name).mkdir()
                return folder / name
            except FileExistsError:
                pass
        n += 1


def _rmtree(p: Path) -> None:
    def onexc(func, path, exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass
    shutil.rmtree(p, onexc=onexc)


def create_backup(src: sqlite3.Connection, folder: Path, *, now: dt.datetime | None = None,
                  vault_root: str | os.PathLike | None = None) -> Path:
    """Write one coordinated backup set and return its folder.

    The database half is a consistent online-backup copy written to a local temp file and moved in, so a sync
    client never sees a partial file. The vault half copies (verified) every blob the copy references that the
    shared pool does not have yet. ``COMPLETE`` is written last.
    """
    now = now or dt.datetime.now()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    vault = VaultStore(vault_root if vault_root is not None else default_vault_root(src))
    fd, tmp_name = tempfile.mkstemp(prefix="reframe-backup-", suffix=".sqlite")
    os.close(fd)
    tmp = Path(tmp_name)
    set_dir: Path | None = None
    try:
        dst = sqlite3.connect(tmp_name)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
            blobs = _db_blobs(dst)
        finally:
            dst.close()
        if integrity != "ok":
            raise BackupError(f"The database copy failed the integrity check: {integrity}")
        set_dir = _new_set_dir(folder, now)
        shutil.move(str(tmp), str(set_dir / DB_NAME))
        _write_json(set_dir / MANIFEST, {"format": FORMAT, "blobs": blobs})
        objects = pool(folder)
        added, missing, corrupt = 0, [], []
        for sha in blobs:
            if objects.has(sha):
                continue
            try:
                added += int(objects.import_blob(vault, sha))
            except BlobMissing:
                missing.append(sha)
            except BlobCorrupt:
                corrupt.append(sha)
        absent = [s for s in blobs if s not in missing and s not in corrupt and not objects.has(s)]
        if absent:
            raise BackupError(f"Backup set {set_dir.name}: {len(absent)} version(s) were not written: "
                              + ", ".join(s[:12] for s in absent[:10]))
        if missing or corrupt:
            print(f"[reframe] backup {set_dir.name}: the live vault lacks {len(missing)} and has {len(corrupt)} "
                  f"corrupt version(s) referenced by the database: "
                  + ", ".join(s[:12] for s in (missing + corrupt)[:10]), file=sys.stderr)
        _write_json(set_dir / MARKER, {
            "complete": True, "format": FORMAT, "created_at": now.isoformat(timespec="seconds"),
            "db_integrity": integrity, "db_bytes": (set_dir / DB_NAME).stat().st_size,
            "vault_blobs": len(blobs), "vault_blobs_added": added, "vault_root": str(vault.root),
            "vault_missing": missing, "vault_corrupt": corrupt})
        return set_dir
    except BaseException:
        if set_dir is not None and not (set_dir / MARKER).exists():
            _rmtree(set_dir)                        # never leave a half-written set behind if we can help it
        raise
    finally:
        if tmp.exists():
            tmp.unlink()


# ------------------------------------------------------------------ retention

def _manifest_blobs(p: Path) -> set[str] | None:
    try:
        return set(json.loads((p / MANIFEST).read_text(encoding="utf-8"))["blobs"])
    except FileNotFoundError:
        return set()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def apply_retention(folder: Path, *, keep_last: int = 20, keep_days: int = 30, keep_months: int = 12,
                    now: dt.datetime | None = None) -> list[Path]:
    """Keep the last N backups plus the newest of each of the last D days and M months. Then delete pool blobs
    that no remaining set references (never content a retained set needs). Returns the removed backups."""
    folder = Path(folder)
    now = now or dt.datetime.now()
    backups = list_backups(folder)  # newest first
    keep: set[Path] = set(backups[:keep_last])
    seen_days: set[dt.date] = set()
    seen_months: set[tuple[int, int]] = set()
    for p in backups:
        t = _backup_time(p)
        if (now - t).days < keep_days and t.date() not in seen_days:
            seen_days.add(t.date())
            keep.add(p)
        month_age = (now.year - t.year) * 12 + now.month - t.month
        if month_age < keep_months and (t.year, t.month) not in seen_months:
            seen_months.add((t.year, t.month))
            keep.add(p)
    removed = []
    for p in backups:
        if p not in keep:
            if p.is_dir():
                _rmtree(p)
            else:
                p.unlink()
            removed.append(p)
    for p in list_incomplete(folder):          # interrupted sets: only when clearly abandoned
        try:
            if time.time() - p.stat().st_mtime > STALE_INCOMPLETE_S:
                _rmtree(p)
        except OSError:
            pass
    gc_pool(folder)
    return removed


def gc_pool(folder: Path) -> int:
    """Delete pool blobs referenced by no set folder still present (complete or not). Returns bytes freed."""
    folder = Path(folder)
    objects = pool(folder)
    if not objects.objects.is_dir():
        return 0
    needed: set[str] = set()
    for p in folder.iterdir():
        if _is_set_dir(p):
            b = _manifest_blobs(p)
            if b is None:                       # unreadable manifest: keep everything
                return 0
            needed |= b
    return sum(objects.delete(s) for s in list(objects.iter_shas()) if s not in needed)


def backup_if_stale(src: sqlite3.Connection, folder: Path, max_age_s: float = 3600) -> Path | None:
    age = newest_age_seconds(folder)
    if age is None or age > max_age_s:
        return create_backup(src, folder)
    return None


# ------------------------------------------------------------------ restore

def restore_backup(db_path: Path, backup_set: Path, folder: Path,
                   vault_root: str | os.PathLike | None = None) -> Path:
    """Replace the live database (and add the vault content it needs) from a backup set.

    1. Safety set of the current state (never overwrite without one).
    2. Verify the chosen set: complete marker, ``PRAGMA integrity_check``, and every version its database
       references present and hash-verified in the live vault or the pool.
    3. Stage the database copy locally and check it again.
    4. Copy the missing blobs into the live vault (additive; content-addressed), THEN switch the live database
       with the SQLite backup API, so the restored database never references missing vault content.
    Returns the safety set.
    """
    backup_set = Path(backup_set)
    marker: dict[str, Any] = {}
    if backup_set.is_dir():
        if not is_complete(backup_set):
            raise BackupError(f"Backup set {backup_set.name} is incomplete and cannot be restored")
        marker = backup_info(backup_set)
    db_file = backup_db_file(backup_set)
    live = connect(db_path)
    staging: Path | None = None
    try:
        vault = VaultStore(vault_root if vault_root is not None else default_vault_root(live))
        safety = create_backup(live, folder, vault_root=vault.root)
        check = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise BackupError(f"Backup {backup_set.name} failed the integrity check: {result}")
            fd, staging_name = tempfile.mkstemp(prefix="reframe-restore-", suffix=".sqlite")
            os.close(fd)
            staging = Path(staging_name)
            st = sqlite3.connect(staging_name)
            try:
                check.backup(st)
            finally:
                st.close()
        finally:
            check.close()
        st = sqlite3.connect(str(staging))
        try:
            if st.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError(f"The staged copy of {backup_set.name} failed the integrity check")
            blobs = _db_blobs(st)
        finally:
            st.close()
        objects = pool(folder)
        from_pool, unavailable = [], []
        for sha in blobs:
            if vault.has(sha) and vault.verify(sha):
                continue
            if objects.has(sha) and objects.verify(sha):
                from_pool.append(sha)
            else:
                unavailable.append(sha)
        known = set(marker.get("vault_missing", [])) | set(marker.get("vault_corrupt", []))
        bad = [s for s in unavailable if s not in known] if backup_set.is_dir() else []
        if bad:
            raise BackupError(f"Backup set {backup_set.name} cannot be restored: {len(bad)} saved version(s) are "
                              f"missing or corrupt: " + ", ".join(s[:12] for s in bad[:10]))
        if from_pool:
            vault.ensure()
        for sha in from_pool:
            vault.import_blob(objects, sha)
        src = sqlite3.connect(str(staging))
        try:
            src.backup(live)
        finally:
            src.close()
        live.execute("PRAGMA journal_mode=WAL")
        return safety
    finally:
        live.close()
        if staging is not None and staging.exists():
            staging.unlink()
