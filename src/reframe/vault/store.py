"""Content-addressed vault store (§10.3 candidate (c), selected by the gate).

Layout under ``root``::

    objects/ab/cdef....zlib   zlib stream of the original bytes; name = SHA-256 of the ORIGINAL content
    staging/                  in-progress writes (deleted by ``recover()``)

Writes are staged: compress into ``staging/``, fsync, re-read + decompress + re-hash, then ``os.replace`` into
``objects/``. A blob that already exists is never rewritten (whole-file deduplication). Hard links are never
used: they would share the bytes with the source and an in-place overwrite would change the "copy".
"""
from __future__ import annotations

import dataclasses
import errno
import hashlib
import json
import os
import secrets
import sqlite3
import zlib
from pathlib import Path
from typing import BinaryIO, Iterator

CHUNK = 1 << 20
ZLIB_LEVEL = 1          # gate: level 6 stores text ~29 % smaller but captures ~2.6x slower; most attachments are pre-compressed
INCOMPRESSIBLE_RATIO = 0.95
BLOB_SUFFIX = ".zlib"
_SHA_CHARS = set("0123456789abcdef")


class VaultError(Exception):
    """A vault operation failed; the message names the version or file involved."""


class BlobMissing(VaultError):
    pass


class BlobCorrupt(VaultError):
    pass


@dataclasses.dataclass
class Staged:
    sha256: str
    size: int            # logical (original) bytes
    stored_size: int     # compressed bytes
    tmp: Path


def valid_sha(sha: str) -> bool:
    return isinstance(sha, str) and len(sha) == 64 and set(sha) <= _SHA_CHARS


def default_vault_root(conn: sqlite3.Connection | None = None) -> Path:
    """The configured vault folder (setting ``vault_dir``), else ``<data_dir>/vault`` (R-166)."""
    from ..db.connection import data_dir
    if conn is not None:
        try:
            r = conn.execute("SELECT value FROM setting WHERE key='vault_dir'").fetchone()
        except sqlite3.Error:
            r = None
        if r is not None:
            try:
                v = json.loads(r[0])
            except ValueError:
                v = None
            if isinstance(v, str) and v.strip():
                return Path(v.strip())
    return data_dir() / "vault"


def is_synced_folder(path: str | os.PathLike) -> bool:
    """True when ``path`` is inside a OneDrive (synced) folder; the live vault must not be (R-166)."""
    p = os.path.normcase(os.path.abspath(str(path)))
    for var in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        base = os.environ.get(var)
        if base:
            b = os.path.normcase(os.path.abspath(base))
            if p == b or p.startswith(b.rstrip("\\/") + os.sep):
                return True
    return "\\onedrive" in p.replace("/", "\\")


def _fsync(fh) -> None:
    fh.flush()
    os.fsync(fh.fileno())


class VaultStore:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.staging = self.root / "staging"

    # ------------------------------------------------------------ layout
    def ensure(self) -> None:
        self.objects.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def blob_path(self, sha: str) -> Path:
        if not valid_sha(sha):
            raise ValueError(f"not a SHA-256: {sha!r}")
        return self.objects / sha[:2] / (sha[2:] + BLOB_SUFFIX)

    @staticmethod
    def backend_ref(sha: str) -> str:
        """Locator stored in ``file_version.backend_ref`` (relative to the vault root)."""
        return f"objects/{sha[:2]}/{sha[2:]}{BLOB_SUFFIX}"

    def has(self, sha: str) -> bool:
        try:
            return self.blob_path(sha).is_file()
        except ValueError:
            return False

    def iter_shas(self) -> Iterator[str]:
        if not self.objects.is_dir():
            return
        for d in self.objects.iterdir():
            if not d.is_dir() or len(d.name) != 2:
                continue
            for f in d.iterdir():
                if f.name.endswith(BLOB_SUFFIX):
                    sha = d.name + f.name[: -len(BLOB_SUFFIX)]
                    if valid_sha(sha):
                        yield sha

    # ------------------------------------------------------------ writes
    def _new_tmp(self) -> Path:
        self.staging.mkdir(parents=True, exist_ok=True)
        return self.staging / f"{secrets.token_hex(8)}.part"

    def stage_stream(self, src: BinaryIO) -> Staged:
        """Compress ``src`` into a staging file while hashing it. Raises OSError (e.g. ENOSPC) after cleanup."""
        tmp = self._new_tmp()
        h = hashlib.sha256()
        size = stored = 0
        try:
            first = src.read(CHUNK)
            comp = zlib.compressobj(_choose_level(first))
            with open(tmp, "xb") as out:
                chunk = first
                while chunk:
                    h.update(chunk)
                    size += len(chunk)
                    data = comp.compress(chunk)
                    if data:
                        out.write(data)
                        stored += len(data)
                    chunk = src.read(CHUNK)
                data = comp.flush()
                out.write(data)
                stored += len(data)
                _fsync(out)
        except BaseException:
            _unlink_quiet(tmp)
            raise
        return Staged(h.hexdigest(), size, stored, tmp)

    def discard(self, staged: Staged) -> None:
        _unlink_quiet(staged.tmp)

    def commit(self, staged: Staged) -> bool:
        """Move a verified staged blob into place. Returns False when the content was already stored."""
        try:
            if self.has(staged.sha256):
                return False
            sha, size = _hash_compressed(staged.tmp)
            if sha != staged.sha256 or size != staged.size:
                raise BlobCorrupt(f"staged copy of {staged.sha256[:12]} failed verification")
            dest = self.blob_path(staged.sha256)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged.tmp, dest)
            return True
        finally:
            _unlink_quiet(staged.tmp)

    def put_file(self, path: str | os.PathLike) -> tuple[str, int, bool]:
        with open(path, "rb") as fh:
            st = self.stage_stream(fh)
        return st.sha256, st.size, self.commit(st)

    def put_bytes(self, data: bytes) -> str:
        import io
        st = self.stage_stream(io.BytesIO(data))
        self.commit(st)
        return st.sha256

    def import_blob(self, other: "VaultStore", sha: str) -> bool:
        """Copy one compressed blob from ``other`` (verified). Returns False when already present."""
        if self.has(sha):
            return False
        src = other.blob_path(sha)
        if not src.is_file():
            raise BlobMissing(f"version {sha[:12]} is missing from {other.root}")
        tmp = self._new_tmp()
        try:
            with open(src, "rb") as fi, open(tmp, "xb") as fo:
                while True:
                    b = fi.read(CHUNK)
                    if not b:
                        break
                    fo.write(b)
                _fsync(fo)
            got, _ = _hash_compressed(tmp)
            if got != sha:
                raise BlobCorrupt(f"version {sha[:12]} in {other.root} is corrupt")
            dest = self.blob_path(sha)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
            return True
        finally:
            _unlink_quiet(tmp)

    def delete(self, sha: str) -> int:
        """Remove one blob; returns the bytes freed. Callers decide whether it is unreferenced."""
        p = self.blob_path(sha)
        try:
            n = p.stat().st_size
            p.unlink()
            return n
        except FileNotFoundError:
            return 0

    # ------------------------------------------------------------- reads
    def iter_chunks(self, sha: str) -> Iterator[bytes]:
        p = self.blob_path(sha)
        if not p.is_file():
            raise BlobMissing(f"version {sha[:12]} is not in the vault ({self.root})")
        d = zlib.decompressobj()
        try:
            with open(p, "rb") as fh:
                while True:
                    b = fh.read(CHUNK)
                    if not b:
                        break
                    out = d.decompress(b)
                    if out:
                        yield out
            tail = d.flush()
        except zlib.error as e:
            raise BlobCorrupt(f"version {sha[:12]} is corrupt: {e}") from None
        if tail:
            yield tail
        if not d.eof:
            raise BlobCorrupt(f"version {sha[:12]} is truncated")

    def open_reader(self, sha: str) -> "BlobReader":
        return BlobReader(self.iter_chunks(sha))

    def verify(self, sha: str) -> bool:
        try:
            h = hashlib.sha256()
            for b in self.iter_chunks(sha):
                h.update(b)
            return h.hexdigest() == sha
        except VaultError:
            return False

    def restore_to(self, sha: str, dest: str | os.PathLike, *, overwrite: bool = False) -> Path:
        """Write the original bytes of ``sha`` to ``dest`` (verified). Never overwrites unless asked."""
        dest = Path(dest)
        if dest.exists() and not overwrite:
            raise FileExistsError(errno.EEXIST, "File already exists", str(dest))
        tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.partial")
        h = hashlib.sha256()
        try:
            with open(tmp, "xb") as out:
                for b in self.iter_chunks(sha):
                    h.update(b)
                    out.write(b)
                _fsync(out)
            if h.hexdigest() != sha:
                raise BlobCorrupt(f"version {sha[:12]} failed verification on restore")
            if overwrite:
                os.replace(tmp, dest)
            else:
                os.rename(tmp, dest)       # Windows: fails if dest appeared meanwhile
            return dest
        finally:
            _unlink_quiet(tmp)

    # ------------------------------------------------------- maintenance
    def recover(self) -> int:
        """Delete leftovers of interrupted writes (startup recovery). Returns the number removed."""
        n = 0
        if self.staging.is_dir():
            for f in self.staging.iterdir():
                if f.is_file() and _unlink_quiet(f):
                    n += 1
        return n

    def size_info(self, conn: sqlite3.Connection | None = None) -> dict:
        """Stored bytes on disk, logical bytes of all recorded versions, and the savings (R-166)."""
        stored = blobs = 0
        if self.objects.is_dir():
            for sha in self.iter_shas():
                try:
                    stored += self.blob_path(sha).stat().st_size
                    blobs += 1
                except OSError:
                    pass
        out = {"stored_bytes": stored, "blobs": blobs, "versions": None, "logical_bytes": None,
               "unique_bytes": None, "savings_bytes": None}
        if conn is not None:
            r = conn.execute("SELECT count(*) n, coalesce(sum(size), 0) s FROM file_version").fetchone()
            u = conn.execute("SELECT coalesce(sum(size), 0) FROM "
                             "(SELECT sha256, max(size) size FROM file_version GROUP BY sha256)").fetchone()[0]
            out.update(versions=r[0], logical_bytes=r[1], unique_bytes=u, savings_bytes=max(0, r[1] - stored))
        return out


class BlobReader:
    """Minimal read-only file object over a decompressing iterator."""

    def __init__(self, chunks: Iterator[bytes]):
        self._it = chunks
        self._buf = b""
        self._eof = False

    def read(self, n: int = -1) -> bytes:
        while not self._eof and (n < 0 or len(self._buf) < n):
            try:
                self._buf += next(self._it)
            except StopIteration:
                self._eof = True
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        self._eof = True
        self._buf = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _choose_level(first: bytes) -> int:
    """Level 0 (stored deflate blocks, same zlib format) when a 256 KB probe shows the data is already compressed:
    PDFs, Office files, images and archives gain nothing from deflate but cost ~30 ms/MB (gate measurement)."""
    probe = first[: 256 * 1024]
    if len(probe) >= 16 * 1024 and len(zlib.compress(probe, 1)) > INCOMPRESSIBLE_RATIO * len(probe):
        return 0
    return ZLIB_LEVEL


def _hash_compressed(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    d = zlib.decompressobj()
    size = 0
    try:
        with open(path, "rb") as fh:
            while True:
                b = fh.read(CHUNK)
                if not b:
                    break
                out = d.decompress(b)
                h.update(out)
                size += len(out)
        out = d.flush()
    except zlib.error as e:
        raise BlobCorrupt(f"{path.name}: {e}") from None
    h.update(out)
    size += len(out)
    if not d.eof:
        raise BlobCorrupt(f"{path.name}: truncated")
    return h.hexdigest(), size


def _unlink_quiet(p: Path) -> bool:
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.chmod(p, 0o666)
            p.unlink()
            return True
        except OSError:
            return False
