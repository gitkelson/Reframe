"""Session-only in-memory file index (R-140: discovered files are never stored in the database).

Entries are addressed by opaque ids that are random per session and stable for a path while the session lasts, so
a rescan keeps selections. The API never accepts a path; it resolves ids through this index.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Iterable

from . import dates as datesmod
from .dates import TakenDate
from .roots import path_key, root_name
from .scanner import FileRecord, extension_of

EXIF_PENDING, EXIF_OK, EXIF_NONE, EXIF_UNREADABLE, EXIF_SKIPPED = "pending", "ok", "none", "unreadable", "skipped"

# column order of the compact rows sent to the client (GET /api/files/entries)
ROW_COLUMNS = ["id", "name", "ext", "kind", "day", "src", "size", "online", "root", "pending", "dir"]


@dataclass(slots=True)
class Entry:
    id: str
    path: str
    key: str
    root: int
    parts: tuple[str, ...]
    name: str
    ext: str
    size: int
    mtime: float
    ctime: float
    online_only: bool
    is_image: bool
    seen: int
    exif_state: str
    taken: TakenDate | None


def _exif_state_for(is_image: bool, ext: str, online: bool) -> str:
    if not ((is_image and ext in datesmod.EXIF_EXTS) or ext == ".mp4"):
        return EXIF_SKIPPED
    return EXIF_SKIPPED if online else EXIF_PENDING


class FileIndex:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.by_id: dict[str, Entry] = {}
        self.by_key: dict[str, str] = {}
        self.roots: list[str] = []
        self.root_names: list[str] = []
        self.version = 0
        self._gen = 0
        self._payload_cache: dict[str, tuple[int, dict]] = {}

    # ------------------------------------------------------------- mutation

    def reset(self, roots: list[str]) -> None:
        with self.lock:
            self.roots = list(roots)
            self.root_names = [root_name(r) for r in roots]
            self.by_id.clear()
            self.by_key.clear()
            self.version += 1

    def begin(self) -> int:
        with self.lock:
            self._gen += 1
            return self._gen

    def add_batch(self, records: Iterable[FileRecord], gen: int) -> int:
        changed = 0
        with self.lock:
            for r in records:
                key = path_key(r.path)
                eid = self.by_key.get(key)
                ext = extension_of(r.name)
                is_image = ext in datesmod.IMAGE_EXTS
                e = self.by_id.get(eid) if eid else None
                if e is not None:
                    e.seen = gen
                    if (e.size, e.mtime, e.ctime, e.online_only, e.name, e.root, e.parts) == \
                            (r.size, r.mtime, r.ctime, r.online_only, r.name, r.root, r.parts):
                        continue
                    e.size, e.mtime, e.ctime, e.online_only = r.size, r.mtime, r.ctime, r.online_only
                    e.name, e.path, e.root, e.parts, e.ext, e.is_image = r.name, r.path, r.root, r.parts, ext, is_image
                    e.exif_state, e.taken = _exif_state_for(is_image, ext, r.online_only), None
                else:
                    eid = secrets.token_urlsafe(12)
                    while eid in self.by_id:
                        eid = secrets.token_urlsafe(12)
                    self.by_key[key] = eid
                    self.by_id[eid] = Entry(eid, r.path, key, r.root, r.parts, r.name, ext, r.size, r.mtime, r.ctime,
                                            r.online_only, is_image, gen, _exif_state_for(is_image, ext, r.online_only), None)
                changed += 1
            if changed:
                self.version += 1
        return changed

    def finish(self, gen: int, root_indices: Iterable[int]) -> int:
        """Drop entries of the given roots that were not seen in scan ``gen`` (deleted, renamed, now excluded)."""
        roots = set(root_indices)
        with self.lock:
            gone = [e for e in self.by_id.values() if e.root in roots and e.seen != gen]
            for e in gone:
                del self.by_id[e.id]
                self.by_key.pop(e.key, None)
            if gone:
                self.version += 1
            return len(gone)

    def pending_exif(self, limit: int) -> list[tuple[str, str, int, float]]:
        with self.lock:
            out = []
            for e in self.by_id.values():
                if e.exif_state == EXIF_PENDING:
                    out.append((e.id, e.path, e.size, e.mtime))
                    if len(out) >= limit:
                        break
            return out

    def set_exif(self, eid: str, size: int, mtime: float, state: str, taken: TakenDate | None) -> bool:
        with self.lock:
            e = self.by_id.get(eid)
            if e is None or e.size != size or e.mtime != mtime or e.exif_state != EXIF_PENDING:
                return False
            e.exif_state, e.taken = state, taken
            self.version += 1
            return True

    # -------------------------------------------------------------- queries

    def get(self, eid: str) -> Entry | None:
        if not isinstance(eid, str):
            return None
        with self.lock:
            return self.by_id.get(eid)

    def count(self) -> int:
        with self.lock:
            return len(self.by_id)

    def exif_pending_count(self) -> int:
        with self.lock:
            return sum(1 for e in self.by_id.values() if e.exif_state == EXIF_PENDING)

    def payload(self, mode: str) -> dict:
        """Compact entries for the client: rows sorted by (timestamp, normalized path) plus an undated list."""
        if mode not in datesmod.MODES:
            raise ValueError("mode must be 'created' or 'modified'")
        with self.lock:
            hit = self._payload_cache.get(mode)
            if hit and hit[0] == self.version:
                return hit[1]
            version = self.version
            entries = sorted(self.by_id.values(), key=lambda e: e.key)
            roots, root_names = list(self.roots), list(self.root_names)
        return self._build(mode, version, entries, roots, root_names)

    def _build(self, mode: str, version: int, entries: list[Entry], roots: list[str], root_names: list[str]) -> dict:
        multi = len(root_names) > 1
        dated: list[tuple[float, str, list]] = []
        undated: list[list] = []
        codes = datesmod.SOURCE_CODES
        for e in entries:
            day, ts, src = datesmod.choose(mode, ctime=e.ctime, mtime=e.mtime, taken=e.taken)
            pending = 1 if (mode == "created" and e.exif_state == EXIF_PENDING) else 0
            rel = "\\".join(((root_names[e.root],) if multi else ()) + e.parts)
            row = [e.id, e.name, e.ext, 1 if e.is_image else 0, None, codes[src], e.size,
                   1 if e.online_only else 0, e.root, pending, rel]
            if day is None:
                undated.append(row)
            else:
                row[4] = datesmod.day_number(day)
                dated.append((ts, e.key, row))
        dated.sort(key=lambda t: (t[0], t[1]))
        undated.sort(key=lambda r: (r[1].lower(), r[10].lower()))
        out = {
            "version": version,
            "mode": mode,
            "columns": ROW_COLUMNS,
            "sources": {str(v): datesmod.SOURCE_LABELS[k] for k, v in codes.items()},
            "roots": [{"path": p, "name": n} for p, n in zip(roots, root_names)],
            "rows": [t[2] for t in dated],
            "undated": undated,
        }
        with self.lock:
            self._payload_cache[mode] = (version, out)
        return out
