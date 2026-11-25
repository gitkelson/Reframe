"""voidtools Everything adapter via the official SDK DLL (spec R-120 - R-128).

All SDK calls run on one dedicated worker thread and each query's whole
reset -> set -> query -> read -> copy sequence is serialized there. Results
are copied into plain Python dicts before the next query starts.

The SDK talks to a *running* Everything client over Windows IPC; it does not
index anything itself. Each query looks the client up again, so Reframe
recovers by itself when Everything is restarted.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import os
import re
import subprocess
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

DEFAULT_DLL = Path(__file__).resolve().parents[3] / "vendor" / "everything" / "Everything64.dll"
EXE_CANDIDATES = (
    Path(r"C:\Program Files\Everything\Everything.exe"),
    Path(r"C:\Program Files (x86)\Everything\Everything.exe"),
)

# Everything.h
EVERYTHING_OK = 0
EVERYTHING_ERROR_MEMORY = 1
EVERYTHING_ERROR_IPC = 2
EVERYTHING_ERROR_REGISTERCLASSEX = 3
EVERYTHING_ERROR_CREATEWINDOW = 4
EVERYTHING_ERROR_CREATETHREAD = 5
EVERYTHING_ERROR_INVALIDINDEX = 6
EVERYTHING_ERROR_INVALIDCALL = 7
EVERYTHING_ERROR_INVALIDREQUEST = 8
EVERYTHING_ERROR_INVALIDPARAMETER = 9

EVERYTHING_SORT_NAME_ASCENDING = 1
EVERYTHING_SORT_DATE_MODIFIED_DESCENDING = 14

EVERYTHING_REQUEST_FILE_NAME = 0x00000001
EVERYTHING_REQUEST_PATH = 0x00000002
EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME = 0x00000004
EVERYTHING_REQUEST_SIZE = 0x00000010
EVERYTHING_REQUEST_DATE_MODIFIED = 0x00000040
REQUEST_FLAGS = (EVERYTHING_REQUEST_FILE_NAME | EVERYTHING_REQUEST_PATH | EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME
                 | EVERYTHING_REQUEST_SIZE | EVERYTHING_REQUEST_DATE_MODIFIED)

SORTS = {"modified": EVERYTHING_SORT_DATE_MODIFIED_DESCENDING, "name": EVERYTHING_SORT_NAME_ASCENDING}
SDK_VERSION = 2  # EVERYTHING_SDK_VERSION of the vendored package (see vendor/everything/VERSION.txt)

# Everything reports "unknown" as all bits set (e.g. folder sizes when not indexed).
_UNKNOWN_SIZE = (-1, 0xFFFFFFFFFFFFFFFF)
_UNKNOWN_FILETIME = (0, 0xFFFFFFFFFFFFFFFF, -1)
_FILETIME_EPOCH = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)

MESSAGES = {
    "ok": "",
    "no_matches": "No matches.",
    "not_running": "Everything is not running.",
    "indexing": "Everything is still indexing. Try again in a moment.",
    "timeout": "The search timed out.",
    "missing_sdk": "The search component (Everything SDK DLL) is missing.",
    "error": "Everything search failed unexpectedly.",
    "superseded": "A newer search replaced this one.",
}

_PATH_RE = re.compile(r'^(?:[A-Za-z]:\\|\\\\|https?://|webcal://|file:)', re.IGNORECASE)


def looks_like_path(text: str | None) -> bool:
    """R-120: path mode for drive paths, UNC, http(s)/webcal/file URLs or quoted text."""
    t = (text or "").strip()
    if not t:
        return False
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return True
    return bool(_PATH_RE.match(t))


class MissingSdk(Exception):
    pass


def filetime_to_iso(value: int | None) -> str | None:
    """FILETIME (100 ns ticks since 1601 UTC) to an ISO local time with offset; None when unknown."""
    if value is None or value in _UNKNOWN_FILETIME or value < 0:
        return None
    try:
        t = _FILETIME_EPOCH + dt.timedelta(microseconds=value // 10)
        return t.astimezone().isoformat(timespec="seconds")
    except (OverflowError, ValueError, OSError):
        return None


def _bind(dll: Any) -> Any:
    """Declare argument and return types for every SDK function we call."""
    from ctypes import POINTER, c_int, c_longlong, c_uint32, c_ulonglong, c_void_p, c_wchar_p
    DWORD, BOOL = c_uint32, c_int
    sig = {
        "Everything_Reset": (None, []),
        "Everything_SetSearchW": (None, [c_wchar_p]),
        "Everything_SetMatchPath": (None, [BOOL]),
        "Everything_SetMatchCase": (None, [BOOL]),
        "Everything_SetMatchWholeWord": (None, [BOOL]),
        "Everything_SetRegex": (None, [BOOL]),
        "Everything_SetMax": (None, [DWORD]),
        "Everything_SetOffset": (None, [DWORD]),
        "Everything_SetSort": (None, [DWORD]),
        "Everything_SetRequestFlags": (None, [DWORD]),
        "Everything_QueryW": (BOOL, [BOOL]),
        "Everything_GetLastError": (DWORD, []),
        "Everything_GetNumResults": (DWORD, []),
        "Everything_GetTotResults": (DWORD, []),
        "Everything_GetResultFileNameW": (c_wchar_p, [DWORD]),
        "Everything_GetResultPathW": (c_wchar_p, [DWORD]),
        "Everything_GetResultFullPathNameW": (DWORD, [DWORD, c_void_p, DWORD]),
        "Everything_GetResultSize": (BOOL, [DWORD, POINTER(c_longlong)]),
        "Everything_GetResultDateModified": (BOOL, [DWORD, POINTER(c_ulonglong)]),
        "Everything_IsDBLoaded": (BOOL, []),
        "Everything_GetMajorVersion": (DWORD, []),
        "Everything_GetMinorVersion": (DWORD, []),
        "Everything_GetRevision": (DWORD, []),
        "Everything_GetBuildNumber": (DWORD, []),
    }
    for name, (res, args) in sig.items():
        f = getattr(dll, name)
        f.restype, f.argtypes = res, args
    return dll


class EverythingService:
    """Serialized Everything SDK access on one worker thread."""

    def __init__(self, dll_path: Path | None = None, sdk: Any = None, *, timeout_s: float = 3.0,
                 launcher: Callable[..., Any] | None = None):
        self.dll_path = Path(dll_path).resolve() if dll_path else DEFAULT_DLL
        self.timeout_s = timeout_s
        self._sdk = sdk
        self._launcher = launcher or subprocess.Popen
        self._latest: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="everything")

    # ------------------------------------------------------------------ worker plumbing

    def _load(self) -> Any:
        """Worker thread only. Loads the DLL by absolute path, never via the search order."""
        if self._sdk is None:
            path = self.dll_path
            if not path.is_absolute() or not path.is_file():
                raise MissingSdk(str(path))
            try:
                self._sdk = _bind(ctypes.WinDLL(str(path)))
            except (OSError, AttributeError) as e:
                raise MissingSdk(f"{path}: {e}") from e
        return self._sdk

    def _run(self, fn: Callable[[], dict], timeout_s: float | None) -> dict | None:
        """Run fn on the worker; None on timeout (the native call cannot be force-cancelled)."""
        fut: Future = self._pool.submit(fn)
        try:
            return fut.result(timeout=timeout_s)
        except FutureTimeout:
            fut.cancel()
            return None

    # ------------------------------------------------------------------ search

    def search(self, query: str, *, offset: int = 0, limit: int = 100, sort: str = "modified",
               request_id: str | int = 0, client: str = "default") -> dict:
        if sort not in SORTS:
            raise ValueError(f"sort must be one of {', '.join(SORTS)}")
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 1000))
        base = {"request_id": request_id, "offset": offset, "limit": limit, "results": [], "total": None}
        with self._lock:
            self._latest[client] = request_id

        def job() -> dict:
            with self._lock:
                if self._latest.get(client) != request_id:
                    return self._reply(base, "superseded")
            return self._query(base, query, offset, limit, SORTS[sort])

        try:
            out = self._run(job, self.timeout_s)
        except CancelledError:
            out = self._reply(base, "error", "The search service is stopped.")
        except RuntimeError as e:  # pool shut down
            out = self._reply(base, "error", f"The search service is stopped ({e}).")
        return out if out is not None else self._reply(base, "timeout")

    @staticmethod
    def _reply(base: dict, state: str, message: str | None = None, **extra: Any) -> dict:
        return {**base, "state": state, "message": MESSAGES[state] if message is None else message, **extra}

    def _query(self, base: dict, query: str, offset: int, limit: int, sort: int) -> dict:
        """Worker thread only: the whole serialized reset -> query -> copy sequence."""
        try:
            sdk = self._load()
        except MissingSdk as e:
            return self._reply(base, "missing_sdk", f"{MESSAGES['missing_sdk']} ({e})")
        try:
            sdk.Everything_Reset()
            if not sdk.Everything_IsDBLoaded():
                if sdk.Everything_GetLastError() == EVERYTHING_ERROR_IPC:
                    return self._reply(base, "not_running")
                return self._reply(base, "indexing")
            sdk.Everything_SetSearchW(query)
            sdk.Everything_SetMatchPath(False)
            sdk.Everything_SetMatchCase(False)
            sdk.Everything_SetMatchWholeWord(False)
            sdk.Everything_SetRegex(False)
            sdk.Everything_SetMax(limit)
            sdk.Everything_SetOffset(offset)
            sdk.Everything_SetSort(sort)
            sdk.Everything_SetRequestFlags(REQUEST_FLAGS)
            if not sdk.Everything_QueryW(True):
                code = sdk.Everything_GetLastError()
                if code == EVERYTHING_ERROR_IPC:
                    return self._reply(base, "not_running")
                return self._reply(base, "error", f"{MESSAGES['error']} (SDK error {code})", error_code=code)
            n = int(sdk.Everything_GetNumResults())
            total = int(sdk.Everything_GetTotResults())
            results = [self._copy_result(sdk, i) for i in range(n)]
        except Exception as e:  # noqa: BLE001 - native failures become an explicit state, never an empty list
            return self._reply(base, "error", f"{MESSAGES['error']} ({e})")
        if not results and total == 0:
            return self._reply(base, "no_matches", total=0)
        return self._reply(base, "ok", results=results, total=total)

    @staticmethod
    def _copy_result(sdk: Any, i: int) -> dict:
        n = int(sdk.Everything_GetResultFullPathNameW(i, None, 0))
        full = ""
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            if sdk.Everything_GetResultFullPathNameW(i, buf, n + 1):
                full = buf.value
        name = sdk.Everything_GetResultFileNameW(i)
        folder = sdk.Everything_GetResultPathW(i)
        if not full and folder is not None and name is not None:
            full = folder.rstrip("\\") + "\\" + name if folder else name
        if name is None:
            name = full.rsplit("\\", 1)[-1]
        if folder is None:
            folder = full.rsplit("\\", 1)[0] if "\\" in full else ""
        size_v = ctypes.c_longlong(0)
        size = None
        if sdk.Everything_GetResultSize(i, ctypes.byref(size_v)) and size_v.value not in _UNKNOWN_SIZE \
                and size_v.value >= 0:
            size = int(size_v.value)
        ft = ctypes.c_ulonglong(0)
        modified = filetime_to_iso(ft.value) if sdk.Everything_GetResultDateModified(i, ctypes.byref(ft)) else None
        return {"name": str(name), "folder": str(folder), "path": str(full), "size": size, "modified": modified}

    # ------------------------------------------------------------------ status / control

    def status(self) -> dict:
        out = {"running": False, "everything_version": None, "sdk_version": SDK_VERSION,
               "sdk_dll_version": None, "index_ready": False, "dll_path": str(self.dll_path),
               "sdk_present": True, "message": ""}

        def job() -> dict:
            try:
                sdk = self._load()
            except MissingSdk as e:
                return {**out, "sdk_present": False, "message": f"{MESSAGES['missing_sdk']} ({e})"}
            out["sdk_dll_version"] = _dll_version(self.dll_path)
            sdk.Everything_Reset()
            major = int(sdk.Everything_GetMajorVersion())
            if not major and sdk.Everything_GetLastError() == EVERYTHING_ERROR_IPC:
                return {**out, "message": MESSAGES["not_running"]}
            ver = [major, int(sdk.Everything_GetMinorVersion()), int(sdk.Everything_GetRevision()),
                   int(sdk.Everything_GetBuildNumber())]
            ready = bool(sdk.Everything_IsDBLoaded())
            return {**out, "running": True, "everything_version": ".".join(map(str, ver)),
                    "index_ready": ready, "message": "" if ready else MESSAGES["indexing"]}

        try:
            res = self._run(job, self.timeout_s)
        except Exception as e:  # noqa: BLE001
            return {**out, "message": f"{MESSAGES['error']} ({e})"}
        return res if res is not None else {**out, "message": "Everything did not answer in time."}

    def start_everything(self) -> bool:
        """Launch the installed Everything.exe in the background. Never installs or configures anything."""
        if self.status().get("running"):
            return True
        exe = next((p for p in EXE_CANDIDATES if p.is_file()), None)
        if exe is None:
            return False
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self._launcher([str(exe), "-startup"], creationflags=flags, close_fds=True, cwd=str(exe.parent))
        except OSError:
            return False
        return True

    def stop(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _dll_version(path: Path) -> str | None:
    """File version from the DLL's VERSIONINFO resource; the 2022 SDK build ships none (-> None)."""
    try:
        ver = ctypes.WinDLL(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "version.dll"))
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)) or not ln.value:
            return None
        ms, ls = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 4)).contents[2:4]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except (AttributeError, OSError):
        return None
