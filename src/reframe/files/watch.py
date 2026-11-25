"""Change notifications for the File timeline roots (R-149).

One ``RootWatcher`` thread per root uses ``ReadDirectoryChangesW`` (pywin32) with overlapped I/O and a short wait,
so ``stop()`` returns promptly. Events are not interpreted one by one: any change, an overflow (zero bytes
returned), an error or a lost network share is reported to ``on_change(root_index, reason)`` and the service
coalesces them into a rescan of that root. The watcher is optional: the service accepts ``watcher_factory=None``.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

FILE_LIST_DIRECTORY = 0x0001
BUFFER_BYTES = 64 * 1024
WAIT_MS = 500
RETRY_S = 30.0


class RootWatcher(threading.Thread):
    def __init__(self, root: str, index: int, on_change: Callable[[int, str], None]):
        super().__init__(daemon=True, name=f"files-watch-{index}")
        self.root, self.index, self.on_change = root, index, on_change
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            import pywintypes
            import win32con
            import win32event
            import win32file
        except Exception:
            return                                      # no pywin32: periodic reconciliation still runs
        # FILE_NOTIFY_CHANGE_ FILE_NAME | DIR_NAME | ATTRIBUTES | SIZE | LAST_WRITE | CREATION
        flags = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x40
        failing = False
        delay = RETRY_S
        while not self._halt.is_set():
            handle = None
            event = None
            try:
                handle = win32file.CreateFile(
                    self.root, FILE_LIST_DIRECTORY,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                    None, win32con.OPEN_EXISTING,
                    win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED, None)
                event = win32event.CreateEvent(None, True, False, None)
                ov = pywintypes.OVERLAPPED()
                ov.hEvent = event
                buf = win32file.AllocateReadBuffer(BUFFER_BYTES)
                if failing:                     # the share came back: what changed meanwhile is unknown
                    failing, delay = False, RETRY_S
                    self.on_change(self.index, "recovered")
                while not self._halt.is_set():
                    win32file.ReadDirectoryChangesW(handle, buf, True, flags, ov)
                    while not self._halt.is_set():
                        rc = win32event.WaitForSingleObject(event, WAIT_MS)
                        if rc == win32event.WAIT_OBJECT_0:
                            break
                    if self._halt.is_set():
                        try:
                            win32file.CancelIo(handle)
                        except Exception:
                            pass
                        break
                    n = win32file.GetOverlappedResult(handle, ov, True)
                    self.on_change(self.index, "overflow" if n == 0 else "change")
            except Exception as e:      # share went away, folder deleted, access revoked
                if self._halt.is_set():
                    break
                if not failing:
                    failing = True
                    self.on_change(self.index, f"error: {e}")
                self._halt.wait(delay)
                delay = min(delay * 2, 300.0)
            finally:
                for h in (handle, event):
                    if h is not None:
                        try:
                            h.Close()
                        except Exception:
                            pass


def default_watcher_factory(root: str, index: int, on_change: Callable[[int, str], None]) -> RootWatcher:
    w = RootWatcher(root, index, on_change)
    w.start()
    return w


class Debouncer:
    """Coalesces bursts of change notifications per root into one call after ``quiet_s`` without events
    (but no later than ``max_wait_s`` after the first)."""

    def __init__(self, fire: Callable[[set[int], set[str]], None], quiet_s: float = 2.0, max_wait_s: float = 10.0):
        self.fire, self.quiet_s, self.max_wait_s = fire, quiet_s, max_wait_s
        self._lock = threading.Lock()
        self._roots: set[int] = set()
        self._reasons: set[str] = set()
        self._first = 0.0
        self._last = 0.0
        self._timer: threading.Timer | None = None

    def poke(self, root: int, reason: str) -> None:
        with self._lock:
            now = time.monotonic()
            if not self._roots:
                self._first = now
            self._roots.add(root)
            self._reasons.add(reason.split(":")[0])
            self._last = now
            if self._timer is None:
                self._arm(self.quiet_s)

    def _arm(self, delay: float) -> None:
        self._timer = threading.Timer(delay, self._check)
        self._timer.daemon = True
        self._timer.start()

    def _check(self) -> None:
        with self._lock:
            now = time.monotonic()
            due = now - self._last >= self.quiet_s or now - self._first >= self.max_wait_s
            if not due:
                self._arm(min(self.quiet_s - (now - self._last), self.max_wait_s - (now - self._first)) + 0.01)
                return
            roots, reasons = set(self._roots), set(self._reasons)
            self._roots.clear()
            self._reasons.clear()
            self._timer = None
        if roots:
            self.fire(roots, reasons)

    def cancel(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = None
            self._roots.clear()
            self._reasons.clear()
