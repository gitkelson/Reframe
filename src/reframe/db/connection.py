"""Connections, schema creation and migrations (spec section 5.1, 5.4)."""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

from .build_schema import SCHEMA_VERSION, schema_path

BUSY_TIMEOUT_MS = 5000


def _platform_data_home() -> Path:
    """Per-user, non-synced application data folder of the current platform."""
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"])
    if os.name == "nt":
        return Path.home() / "AppData" / "Local"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def data_dir() -> Path:
    base = os.environ.get("REFRAME_DATA_DIR")
    if base:
        return Path(base)
    return _platform_data_home() / "Reframe"


def default_db_path() -> Path:
    env = os.environ.get("REFRAME_DB")
    return Path(env) if env else data_dir() / "reframe.sqlite"


def connect(path: str | os.PathLike, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with the per-connection PRAGMAs every writer must set.

    ``autocommit=True`` means no implicit transactions: every write goes through
    an explicit ``BEGIN IMMEDIATE`` (see changelog.writer).
    """
    if readonly:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True,
                               autocommit=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), autocommit=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrations_dir() -> Path:
    return Path(__file__).with_name("migrations")


def _migration_files() -> list[tuple[int, Path]]:
    d = _migrations_dir()
    if not d.is_dir():
        return []
    found = []
    for p in d.glob("*.sql"):
        m = re.match(r"(\d+)_", p.name)
        if m:
            found.append((int(m.group(1)), p))
    return sorted(found)


def init_db(path: str | os.PathLike) -> sqlite3.Connection:
    """Create the database if needed and apply forward-only migrations."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    conn.execute("PRAGMA journal_mode=WAL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply(conn, schema_path().read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        version = SCHEMA_VERSION
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"Database schema version {version} is newer than this Reframe ({SCHEMA_VERSION})")
    for number, mig in _migration_files():
        if version < number <= SCHEMA_VERSION:
            migrate(conn, mig.read_text(encoding="utf-8"), number)
            version = number
    missing = _missing_columns(conn)
    if missing:
        conn.close()
        raise SchemaMismatch(
            f"The database {p} says it is schema version {version} but does not match this Reframe "
            f"(missing: {', '.join(missing[:6])}{' ...' if len(missing) > 6 else ''}). It was probably made by "
            "another build. Move this file away to start with an empty database.")
    return conn


class SchemaMismatch(RuntimeError):
    """The file carries the current user_version but its tables differ from schema.sql."""


_expected: dict[str, set[str]] | None = None


def _table_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: {r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')} for t in names}


def _missing_columns(conn: sqlite3.Connection) -> list[str]:
    """Tables/columns of the current schema that the database lacks (extra ones are tolerated)."""
    global _expected
    if _expected is None:
        ref = sqlite3.connect(":memory:")
        try:
            _apply(ref, schema_path().read_text(encoding="utf-8"))
            _expected = _table_columns(ref)
        finally:
            ref.close()
    have = _table_columns(conn)
    out = []
    for table, cols in sorted(_expected.items()):
        if table not in have:
            out.append(table)
        else:
            out += [f"{table}.{c}" for c in sorted(cols - have[table])]
    return out


def migrate(conn: sqlite3.Connection, script: str, number: int) -> None:
    """Apply one forward migration in a single transaction (spec 13.7 D-05).

    Table rebuilds need ``foreign_keys`` OFF, which can only change outside a transaction; the
    foreign keys are checked explicitly before COMMIT instead.
    """
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply(conn, script)
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise sqlite3.IntegrityError(f"migration {number}: foreign key check failed for {len(bad)} row(s)")
            conn.execute(f"PRAGMA user_version={number}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _apply(conn: sqlite3.Connection, script: str) -> None:
    """Run a multi-statement script inside the caller's transaction.

    ``executescript`` commits first, so statements are split with
    ``sqlite3.complete_statement`` instead (triggers contain semicolons).
    """
    buf = ""
    for line in script.splitlines(keepends=True):
        if not buf and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            conn.execute(buf)
            buf = ""
    if buf.strip():
        raise ValueError("incomplete SQL statement in script: " + buf[:80])
