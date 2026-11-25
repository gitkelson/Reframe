"""Change notifications for attached files only (R-163).

One ``DirWatcher`` thread per parent directory of an attached file (never recursive), using
``ReadDirectoryChangesW`` (pywin32) with overlapped I/O so ``stop()`` returns promptly. Notifications are filtered
to the attached file names of that directory. An overflow, an error, or a recovered share is reported with
``name=None`` and the service answers with a reconciliation of that directory's files.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Iterable

from .capture import io_path

FILE_LIST_DIRECTORY = 0x0001
BUFFER_BYTES = 64 * 1024
WAIT_MS = 500
RETRY_S = 30.0
# FILE_NOTIFY_CHANGE_FILE_NAME | SIZE | LAST_WRITE | CREATION
NOTIFY_FLAGS = 0x1 | 0x8 | 0x10 | 0x40

OnEvent = Callable[[str, "str | None", str], None]     # (directory, file name or None, reason)


class DirWatcher(threading.Thread):
    def __init__(self, directory: str, names: Iterable[str], on_event: OnEvent):
        super().__init__(daemon=True, name=f"vault-watch-{os.path.basename(directory)[:20]}")
        self.directory = directory
        self._names = {os.path.normcase(n) for n in names}
        self._lock = threading.Lock()
        self.on_event = on_event
        self._halt = threading.Event()

    def set_names(self, names: Iterable[str]) -> None:
        with self._lock:
            self._names = {os.path.normcase(n) for n in names}

    def wants(self, name: str) -> bool:
        with self._lock:
            return os.path.normcase(name) in self._names

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            import pywintypes
            import win32con
            import win32event
            import win32file
        except Exception:
            return                                  # no pywin32: periodic reconciliation still runs
        failing = False
        delay = RETRY_S
        while not self._halt.is_set():
            handle = event = None
            try:
                handle = win32file.CreateFile(
                    io_path(self.directory), FILE_LIST_DIRECTORY,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                    None, win32con.OPEN_EXISTING,
                    win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED, None)
                event = win32event.CreateEvent(None, True, False, None)
                ov = pywintypes.OVERLAPPED()
                ov.hEvent = event
                buf = win32file.AllocateReadBuffer(BUFFER_BYTES)
                if failing:
                    failing, delay = False, RETRY_S
                    self.on_event(self.directory, None, "recovered")
                while not self._halt.is_set():
                    win32event.ResetEvent(event)
                    win32file.ReadDirectoryChangesW(handle, buf, False, NOTIFY_FLAGS, ov)
                    while not self._halt.is_set():
                        if win32event.WaitForSingleObject(event, WAIT_MS) == win32event.WAIT_OBJECT_0:
                            break
                    if self._halt.is_set():
                        try:
                            win32file.CancelIo(handle)
                        except Exception:
                            pass
                        break
                    n = win32file.GetOverlappedResult(handle, ov, True)
                    if n == 0:
                        self.on_event(self.directory, None, "overflow")
                        continue
                    seen: set[str] = set()
                    for _action, name in win32file.FILE_NOTIFY_INFORMATION(buf, n):
                        base = os.path.basename(name)
                        if base not in seen and self.wants(base):
                            seen.add(base)
                            self.on_event(self.directory, base, "change")
            except Exception as e:               # share went away, folder deleted, access revoked
                if self._halt.is_set():
                    break
                if not failing:
                    failing = True
                    self.on_event(self.directory, None, f"error: {e}")
                self._halt.wait(delay)
                delay = min(delay * 2, 300.0)
            finally:
                for h in (handle, event):
                    if h is not None:
                        try:
                            h.Close()
                        except Exception:
                            pass


class WatchSet:
    """Keeps exactly one watcher per directory that holds attached files."""

    def __init__(self, on_event: OnEvent, factory: Callable[[str, Iterable[str], OnEvent], DirWatcher] | None = None):
        self.on_event = on_event
        self.factory = factory or _start_watcher
        self._dirs: dict[str, DirWatcher] = {}
        self._lock = threading.Lock()

    def update(self, paths: Iterable[str]) -> None:
        wanted: dict[str, tuple[str, set[str]]] = {}
        for p in paths:
            d, name = os.path.split(p)
            if not d or not name:
                continue
            k = os.path.normcase(d)
            wanted.setdefault(k, (d, set()))[1].add(name)
        with self._lock:
            for k in list(self._dirs):
                if k not in wanted:
                    self._dirs.pop(k).stop()
            for k, (d, names) in wanted.items():
                w = self._dirs.get(k)
                if w is None:
                    self._dirs[k] = self.factory(d, names, self.on_event)
                else:
                    w.set_names(names)

    def directories(self) -> list[str]:
        with self._lock:
            return [w.directory for w in self._dirs.values()]

    def stop_all(self) -> None:
        with self._lock:
            for w in self._dirs.values():
                w.stop()
            self._dirs.clear()


def _start_watcher(directory: str, names: Iterable[str], on_event: OnEvent) -> DirWatcher:
    w = DirWatcher(directory, names, on_event)
    w.start()
    return w


class KeyedDebouncer:
    """Fires ``fire(key)`` once per key after ``quiet_s`` without events for it (at most ``max_wait_s`` after the
    first), so a burst of writes during a save becomes one capture."""

    def __init__(self, fire: Callable[[str], None], quiet_s: float = 2.0, max_wait_s: float = 10.0):
        self.fire, self.quiet_s, self.max_wait_s = fire, quiet_s, max_wait_s
        self._pending: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="vault-debounce")
        self._thread.start()

    def poke(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            first, _ = self._pending.get(key, (now, now))
            self._pending[key] = (first, now)
        self._wake.set()

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _run(self) -> None:
        while not self._halt.is_set():
            self._wake.wait(0.25)
            self._wake.clear()
            now = time.monotonic()
            due = []
            with self._lock:
                for k, (first, last) in list(self._pending.items()):
                    if now - last >= self.quiet_s or now - first >= self.max_wait_s:
                        due.append(k)
                        del self._pending[k]
            for k in due:
                try:
                    self.fire(k)
                except Exception:
                    pass

    def stop(self) -> None:
        self._halt.set()
        self._wake.set()
        with self._lock:
            self._pending.clear()
