"""Recursive discovery for the File timeline (R-141, R-148, R-149).

``scan`` walks the roots with ``os.scandir`` (iteratively, no recursion limit) on the caller's thread; the service
runs it on a worker. Results stream through ``on_batch``; ``cancel`` stops the walk between directories and
between batches. Junctions and symbolic links are never followed. Hidden/system entries are skipped unless asked
for. Cloud placeholders are listed from directory metadata only and never opened. Every root gets an explicit
report so unreachable, denied, skipped and empty are distinguishable.
"""
from __future__ import annotations

import ntpath
import os
import re
import stat as statmod
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ATTRS = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C

NO_EXTENSION = "(none)"
SUGGESTED_EXCLUSIONS = [".tmp", ".bak", ".lnk", "~$*", ".ini", "thumbs.db", "desktop.ini"]
MAX_PROBLEMS_LISTED = 200        # per root; the counts stay exact


def suggested_exclusions() -> list[str]:
    """Common junk types offered as one-click additions (R-148). None of them is applied by default."""
    return list(SUGGESTED_EXCLUSIONS)


# ------------------------------------------------------------------ exclusions

def normalize_exclusion(rule: str) -> str | None:
    """Canonical form of one exclusion rule, or None when it is not a valid rule.

    * '' / '.' / '(none)' -> '(none)': files without an extension (dotless names)
    * '.tmp', 'tmp' -> '.tmp' (a suffix rule); multi-part suffixes such as '.tar.gz' need the leading dot
    * patterns with '*' or '?' ('~$*') match the whole file name
    * a name with an inner dot and no leading dot ('thumbs.db') is an exact file name
    A name whose only dot is the first character ('.gitignore') has no extension, so '(none)' matches it.
    Everything is case-insensitive.
    """
    if not isinstance(rule, str):
        return None
    r = rule.strip().lower()
    if r in ("", ".", NO_EXTENSION, "none", "(no extension)"):
        return NO_EXTENSION
    if any(c in r for c in '\\/:"<>|'):
        return None
    if "*" in r or "?" in r:
        return r
    if r.startswith("."):
        return r if len(r) > 1 and ".." not in r else None
    if "." in r:
        return r                     # exact file name such as 'thumbs.db'
    return "." + r


def normalize_exclusions(rules: list[str]) -> tuple[list[str], list[str]]:
    """(canonical unique rules in input order, refused inputs)."""
    out: list[str] = []
    bad: list[str] = []
    for raw in rules if isinstance(rules, list) else []:
        n = normalize_exclusion(raw)
        if n is None:
            bad.append(str(raw))
        elif n not in out:
            out.append(n)
    return out, bad


def _glob_re(pattern: str) -> re.Pattern:
    return re.compile("^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$", re.S)


def has_extension(name: str) -> bool:
    """A dot that is neither the first nor the last character marks an extension ('.gitignore' has none)."""
    i = name.rfind(".")
    return 0 < i < len(name) - 1


class Exclusions:
    """Compiled exclusion rules; ``excluded(name)`` is case-insensitive."""

    def __init__(self, rules: list[str]):
        self.rules, _ = normalize_exclusions(rules)
        self.dotless = NO_EXTENSION in self.rules
        self.suffixes = tuple(r for r in self.rules if r.startswith(".") and r != NO_EXTENSION)
        self.names = frozenset(r for r in self.rules if not r.startswith(".") and "*" not in r and "?" not in r
                               and r != NO_EXTENSION)
        self.patterns = [_glob_re(r) for r in self.rules if "*" in r or "?" in r]

    def excluded(self, name: str) -> bool:
        n = name.lower()
        if self.dotless and not has_extension(n):
            return True
        if self.suffixes and n.endswith(self.suffixes):
            return True
        if n in self.names:
            return True
        return any(p.match(n) for p in self.patterns)


def extension_of(name: str) -> str:
    """Lower-case last extension ('.jpg'), '' for dotless names."""
    return name[name.rfind("."):].lower() if has_extension(name) else ""


# ------------------------------------------------------------------ scanning

@dataclass(slots=True)
class FileRecord:
    path: str
    root: int
    parts: tuple[str, ...]          # parent folder names below the root
    name: str
    size: int
    mtime: float
    ctime: float
    attrs: int
    online_only: bool


@dataclass
class RootReport:
    path: str
    status: str = "pending"          # ok | partial | empty | unreachable | denied | not_a_folder | cancelled
    message: str = ""
    files: int = 0
    dirs: int = 0
    denied: list[str] = field(default_factory=list)
    denied_count: int = 0
    errors: list[str] = field(default_factory=list)
    error_count: int = 0
    links_skipped: int = 0
    hidden_skipped: int = 0
    excluded: int = 0
    online_only: int = 0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("path", "status", "message", "files", "dirs", "denied", "denied_count",
                                               "errors", "error_count", "links_skipped", "hidden_skipped",
                                               "excluded", "online_only")}

    def _deny(self, rel: str) -> None:
        self.denied_count += 1
        if len(self.denied) < MAX_PROBLEMS_LISTED:
            self.denied.append(rel)

    def _error(self, text: str) -> None:
        self.error_count += 1
        if len(self.errors) < MAX_PROBLEMS_LISTED:
            self.errors.append(text)


def _attrs(st: os.stat_result) -> int:
    return getattr(st, "st_file_attributes", 0) or 0


def _creation_time(st: os.stat_result) -> float:
    bt = getattr(st, "st_birthtime", None)
    return float(bt) if bt is not None else float(st.st_ctime)


def _is_link(entry: os.DirEntry, st: os.stat_result | None) -> bool:
    try:
        if entry.is_symlink():
            return True
        if hasattr(entry, "is_junction") and entry.is_junction():
            return True
    except OSError:
        return True
    if st is not None and _attrs(st) & FILE_ATTRIBUTE_REPARSE_POINT:
        tag = getattr(st, "st_reparse_tag", 0)
        return tag in (IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK)
    return False


def scan(roots: list[str], *, exclusions: Exclusions, include_hidden: bool,
         on_batch: Callable[[list[FileRecord]], None],
         on_progress: Callable[[dict], None] | None = None,
         cancel: threading.Event | None = None,
         root_indices: list[int] | None = None,
         batch_size: int = 500, progress_every_s: float = 0.25) -> list[RootReport]:
    """Walk the selected roots (all by default). Returns one RootReport per scanned root, in ``root_indices`` order."""
    cancel = cancel or threading.Event()
    indices = list(range(len(roots))) if root_indices is None else list(root_indices)
    reports: list[RootReport] = []
    batch: list[FileRecord] = []
    totals = {"files": 0, "dirs": 0}
    last_progress = [0.0]

    def flush() -> None:
        if batch:
            on_batch(list(batch))
            batch.clear()

    def progress(current: str, force: bool = False) -> None:
        now = time.monotonic()
        if on_progress and (force or now - last_progress[0] >= progress_every_s):
            last_progress[0] = now
            on_progress({"files": totals["files"], "dirs": totals["dirs"], "current": current})

    for ri in indices:
        root = roots[ri]
        rep = RootReport(path=root)
        reports.append(rep)
        if cancel.is_set():
            rep.status = "cancelled"
            continue
        try:
            st = os.stat(root)
        except PermissionError:
            rep.status, rep.message = "denied", "Access to this folder is denied."
            continue
        except OSError as e:
            rep.status, rep.message = "unreachable", f"Cannot reach this folder ({e.strerror or e})."
            continue
        if not statmod.S_ISDIR(st.st_mode):
            rep.status, rep.message = "not_a_folder", "This path is not a folder."
            continue

        stack: list[tuple[str, tuple[str, ...]]] = [(root, ())]
        root_failed = False
        while stack:
            if cancel.is_set():
                break
            d, parts = stack.pop()
            rel = "\\".join(parts) or "."
            try:
                it = os.scandir(d)
            except PermissionError:
                if not parts:
                    rep.status, rep.message, root_failed = "denied", "Access to this folder is denied.", True
                    break
                rep._deny(rel)
                continue
            except FileNotFoundError:
                if not parts:
                    rep.status, rep.message, root_failed = "unreachable", "The folder disappeared during the scan.", True
                    break
                rep._error(f"{rel}: folder vanished during the scan")
                continue
            except OSError as e:
                if not parts:
                    rep.status, rep.message, root_failed = "unreachable", f"Cannot read this folder ({e.strerror or e}).", True
                    break
                rep._error(f"{rel}: {e.strerror or e}")
                continue
            rep.dirs += 1
            totals["dirs"] += 1
            subdirs: list[tuple[str, tuple[str, ...]]] = []
            with it:
                while True:
                    try:
                        entry = next(it)
                    except StopIteration:
                        break
                    except OSError as e:     # network dropped mid-listing
                        rep._error(f"{rel}: listing stopped ({e.strerror or e})")
                        break
                    try:
                        est = entry.stat(follow_symlinks=False)
                    except OSError as e:
                        rep._error(f"{ntpath.join(rel, entry.name)}: {e.strerror or e}")
                        continue
                    attrs = _attrs(est)
                    if not include_hidden and attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                        rep.hidden_skipped += 1
                        continue
                    if _is_link(entry, est):
                        rep.links_skipped += 1
                        continue
                    if statmod.S_ISDIR(est.st_mode):
                        subdirs.append((entry.path, parts + (entry.name,)))
                        continue
                    if not statmod.S_ISREG(est.st_mode):
                        continue
                    if exclusions.excluded(entry.name):
                        rep.excluded += 1
                        continue
                    online = bool(attrs & CLOUD_ATTRS)
                    if online:
                        rep.online_only += 1
                    batch.append(FileRecord(entry.path, ri, parts, entry.name, int(est.st_size), float(est.st_mtime),
                                            _creation_time(est), attrs, online))
                    rep.files += 1
                    totals["files"] += 1
                    if len(batch) >= batch_size:
                        flush()
                        progress(root)
                        if cancel.is_set():
                            break
            subdirs.sort(key=lambda s: s[0].lower(), reverse=True)    # deterministic, alphabetical walk
            stack.extend(subdirs)
            progress(root)
        flush()
        if root_failed:
            continue
        if cancel.is_set():
            rep.status, rep.message = "cancelled", "The scan was cancelled; the list may be incomplete."
        elif rep.denied_count or rep.error_count:
            rep.status = "partial"
            bits = []
            if rep.denied_count:
                bits.append(f"{rep.denied_count} folder{'s' if rep.denied_count != 1 else ''} denied")
            if rep.error_count:
                bits.append(f"{rep.error_count} read error{'s' if rep.error_count != 1 else ''}")
            rep.message = "Partly scanned: " + ", ".join(bits) + "."
        elif rep.files == 0:
            rep.status, rep.message = "empty", "No files found (after exclusions)."
        else:
            rep.status = "ok"
    flush()
    progress("", force=True)
    return reports
