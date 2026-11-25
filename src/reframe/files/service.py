"""File timeline service (spec 3.12): options, background scanning, media dates, watchers, thumbnails, open/reveal.

One instance per app (``app.config["FILES"]``). Threads: one scan worker, one date worker, one reconciliation timer,
one watcher per root and the bounded thumbnail pool. All of them start lazily or in ``start()`` and end in ``stop()``.
Progress is published on the SSE hub as event type 'files' with a ``type`` field:
'scan' (state started | progress | finished | cancelled | error), 'stale', 'dates', 'options'.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..db.connection import connect, data_dir
from ..domain import settings as settingsmod
from . import dates as datesmod
from .index import EXIF_OK, EXIF_PENDING, FileIndex
from .roots import RootsError, is_within, path_key, root_name, validate_roots
from .scanner import CLOUD_ATTRS, Exclusions, normalize_exclusions, scan, suggested_exclusions
from .thumbs import REASON_ONLINE_ONLY, ThumbResult, ThumbService
from .watch import Debouncer, default_watcher_factory

ALL = "all"
RECONCILE_S = 600.0
EXIF_BATCH = 32
PUBLISH_PROGRESS_S = 0.5


class OpenRefused(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class DefaultLauncher:
    def open(self, path: str) -> None:
        os.startfile(path)  # type: ignore[attr-defined]

    def reveal(self, path: str) -> None:
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class FileTimelineService:
    def __init__(self, db_path: str | Path, hub: Any = None, *,
                 watcher_factory: Callable | None = default_watcher_factory,
                 thumbs: ThumbService | None = None, cache_dir: str | Path | None = None,
                 reconcile_s: float = RECONCILE_S, launcher: Any = None, exif: bool = True):
        self.db_path = str(db_path)
        self.hub = hub
        self.index = FileIndex()
        self.watcher_factory = watcher_factory
        self.reconcile_s = reconcile_s
        self.launcher = launcher or DefaultLauncher()
        self.exif_enabled = exif
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._thumbs = thumbs
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._options: dict[str, Any] = {"roots": [], "excluded_ext": [], "include_hidden": False}
        self._loaded = False
        self._started = False
        self._stopping = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._pending: Any = None
        self._cancel = threading.Event()
        self._scanning = False
        self._epoch = 0
        self._reports: list[dict | None] = []
        self._ever_scanned = False
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._progress: dict[str, Any] = {"files": 0, "dirs": 0, "current": ""}
        self._stale_reason = ""
        self._last_error = ""
        self._watchers: list[Any] = []
        self._debouncer = Debouncer(self._on_debounced)
        self._exif_wake = threading.Event()
        self._scan_done = threading.Event()

    # ---------------------------------------------------------------- events

    def _publish(self, kind: str, **data: Any) -> None:
        if self.hub is not None:
            try:
                self.hub.publish("files", {"type": kind, "version": self.index.version, **data})
            except Exception:
                pass

    # --------------------------------------------------------------- options

    def _read_settings(self) -> dict[str, Any]:
        conn = connect(self.db_path)
        try:
            roots = settingsmod.get(conn, "files_roots") or []
            excl = settingsmod.get(conn, "files_excluded_ext") or []
            hidden = bool(settingsmod.get(conn, "files_include_hidden"))
        finally:
            conn.close()
        good: list[str] = []
        for r in roots if isinstance(roots, list) else []:     # tolerate a list written around the API
            try:
                good, _ = validate_roots(good + [r], probe=False)
            except RootsError:
                pass
        excl_n, _ = normalize_exclusions(excl if isinstance(excl, list) else [])
        return {"roots": good, "excluded_ext": excl_n, "include_hidden": hidden}

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            opts = self._read_settings()
            self._apply(opts, initial=True)
            self._loaded = True

    def _apply(self, opts: dict[str, Any], *, initial: bool = False) -> bool:
        """Adopt new options. Returns True when anything changed (the caller requests a scan)."""
        with self._lock:
            old = self._options
            roots_changed = [path_key(r) for r in old["roots"]] != [path_key(r) for r in opts["roots"]]
            changed = initial or roots_changed or old["excluded_ext"] != opts["excluded_ext"] \
                or old["include_hidden"] != opts["include_hidden"]
            self._options = {"roots": list(opts["roots"]), "excluded_ext": list(opts["excluded_ext"]),
                             "include_hidden": bool(opts["include_hidden"])}
            if initial or roots_changed:
                self._epoch += 1
                self._cancel.set()
                self._pending = None
                self.index.reset(self._options["roots"])
                self._reports = [None] * len(self._options["roots"])
                self._ever_scanned = False if not initial else self._ever_scanned
                self._stale_reason = ""
                if not initial:
                    self._restart_watchers(force=True)
            elif changed:
                self._cancel.set()
            return changed

    def options(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            return {**{k: (list(v) if isinstance(v, list) else v) for k, v in self._options.items()},
                    "suggested_exclusions": suggested_exclusions()}

    def set_options(self, roots: list[str] | None = None, excluded_ext: list[str] | None = None,
                    include_hidden: bool | None = None) -> dict[str, Any]:
        """Validate, persist (setting table) and apply. Raises RootsError / ValueError with every problem listed."""
        self._ensure_loaded()
        with self._lock:
            cur = dict(self._options)
        warnings: list[str] = []
        if roots is not None:
            cur["roots"], warnings = validate_roots(roots)
        if excluded_ext is not None:
            if not isinstance(excluded_ext, list):
                raise ValueError("excluded_ext must be a list of extensions or name patterns.")
            norm, bad = normalize_exclusions(excluded_ext)
            if bad:
                raise ValueError("Not a valid extension or name pattern: " + ", ".join(repr(b) for b in bad)
                                 + ". Use forms like .tmp, .tar.gz, ~$* or thumbs.db (no folders).")
            cur["excluded_ext"] = norm
        if include_hidden is not None:
            if not isinstance(include_hidden, bool):
                raise ValueError("include_hidden must be true or false.")
            cur["include_hidden"] = include_hidden
        conn = connect(self.db_path)
        try:
            settingsmod.set_many(conn, {"files_roots": cur["roots"], "files_excluded_ext": cur["excluded_ext"],
                                        "files_include_hidden": cur["include_hidden"]})
        finally:
            conn.close()
        if self._apply(cur) and cur["roots"]:
            self._request(ALL)
        self._publish("options")
        return {"options": self.options(), "warnings": warnings}

    # ------------------------------------------------------------------ scan

    def rescan(self, roots: Any = ALL, reason: str = "manual") -> dict[str, Any]:
        """Request a scan of all roots (or a set of root indices). Settings changed behind the service's back are
        picked up first."""
        self._ensure_loaded()
        try:
            fresh = self._read_settings()
            if self._apply(fresh):
                roots = ALL
        except Exception:
            pass
        if not self._options["roots"]:
            return self.status()
        self._request(roots, reason)
        return self.status()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            self._pending = None
            if self._scanning:
                self._cancel.set()
        return self.status()

    def _request(self, roots: Any = ALL, reason: str = "manual") -> None:
        with self._cond:
            if roots == ALL or self._pending == ALL:
                self._pending = ALL
            else:
                self._pending = set(self._pending or ()) | {int(r) for r in roots}
            self._scan_done.clear()
            if reason in ("change", "overflow", "error", "recovered"):
                self._stale_reason = self._stale_reason or "Files changed; refreshing."
            self._cond.notify_all()
        self._ensure_thread("scan", self._scan_loop)

    def _ensure_thread(self, name: str, target: Callable[[], None]) -> None:
        with self._lock:
            t = self._threads.get(name)
            if t is not None and t.is_alive():
                return
            if self._stopping.is_set():
                return
            t = threading.Thread(target=target, daemon=True, name=f"files-{name}")
            self._threads[name] = t
            t.start()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Test/host helper: block until no scan is pending or running."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._pending is None and not self._scanning:
                    return True
            self._scan_done.wait(0.05)
        return False

    def _scan_loop(self) -> None:
        while not self._stopping.is_set():
            with self._cond:
                while self._pending is None and not self._stopping.is_set():
                    self._cond.wait(1.0)
                if self._stopping.is_set():
                    return
                req, self._pending = self._pending, None
                opts = dict(self._options)
                roots = list(opts["roots"])
                epoch = self._epoch
                self._cancel = cancel = threading.Event()
                self._scanning = True
                self._started_at = _now_iso()
                self._progress = {"files": 0, "dirs": 0, "current": ""}
                indices = list(range(len(roots))) if req == ALL else sorted(i for i in req if 0 <= i < len(roots))
            try:
                self._run_scan(roots, indices, opts, epoch, cancel)
            except Exception as e:  # never let the worker die silently
                with self._lock:
                    self._last_error = f"Scan failed: {e}"
                self._publish("scan", state="error", message=f"Scan failed: {e}")
            finally:
                with self._lock:
                    self._scanning = False
                    if self._pending is None:
                        self._scan_done.set()

    def _run_scan(self, roots: list[str], indices: list[int], opts: dict, epoch: int,
                  cancel: threading.Event) -> None:
        if not indices:
            return
        gen = self.index.begin()
        self._publish("scan", state="started", roots=indices)
        last_pub = [0.0]

        def on_batch(records):
            with self._lock:
                if epoch != self._epoch:
                    cancel.set()
                    return
            self.index.add_batch(records, gen)

        def on_progress(p):
            with self._lock:
                self._progress = dict(p)
            now = time.monotonic()
            if now - last_pub[0] >= PUBLISH_PROGRESS_S:
                last_pub[0] = now
                self._publish("scan", state="progress", files=p["files"], dirs=p["dirs"], current=p.get("current", ""))

        reports = scan(roots, exclusions=Exclusions(opts["excluded_ext"]), include_hidden=opts["include_hidden"],
                       on_batch=on_batch, on_progress=on_progress, cancel=cancel, root_indices=indices)
        with self._lock:
            if epoch != self._epoch:
                return
        prune = [ri for ri, rep in zip(indices, reports) if rep.status in ("ok", "partial", "empty")]
        self.index.finish(gen, prune)
        cancelled = any(r.status == "cancelled" for r in reports)
        with self._lock:
            for ri, rep in zip(indices, reports):
                d = rep.as_dict()
                d["name"] = root_name(roots[ri])
                d["scanned_at"] = _now_iso()
                self._reports[ri] = d
            self._ever_scanned = True
            self._finished_at = _now_iso()
            unreachable = [r.path for r in reports if r.status in ("unreachable", "denied")]
            if cancelled:
                self._stale_reason = "The last scan was cancelled; the list may be incomplete. Refresh to rescan."
            elif unreachable:
                self._stale_reason = "Some roots could not be reached; their files may be out of date."
            else:
                self._stale_reason = ""
            self._last_error = ""
        self._publish("scan", state="cancelled" if cancelled else "finished", files=self.index.count(),
                      partial=self._is_partial(), stale=bool(self._stale_reason))
        self._exif_wake.set()
        if self.exif_enabled:
            self._ensure_thread("exif", self._exif_loop)
        self._restart_watchers()

    def _is_partial(self) -> bool:
        with self._lock:
            return any(r and r["status"] in ("partial", "cancelled", "unreachable", "denied", "not_a_folder")
                       for r in self._reports)

    # ---------------------------------------------------------- media dates

    def _exif_loop(self) -> None:
        last_pub = 0.0
        dirty = False
        while not self._stopping.is_set():
            batch = self.index.pending_exif(EXIF_BATCH)
            if not batch:
                if dirty:
                    self._publish("dates", pending=0)
                    dirty = False
                self._exif_wake.wait(5.0)
                self._exif_wake.clear()
                continue
            for eid, path, size, mtime in batch:
                if self._stopping.is_set():
                    return
                state, taken = datesmod.read_taken(path)
                if self.index.set_exif(eid, size, mtime, EXIF_OK if state == "ok" else state, taken):
                    dirty = True
            now = time.monotonic()
            if dirty and now - last_pub >= 1.0:
                last_pub = now
                self._publish("dates", pending=self.index.exif_pending_count())
                dirty = False

    def process_exif_now(self) -> int:
        """Synchronously resolve all pending media dates (tests and tools)."""
        n = 0
        while True:
            batch = self.index.pending_exif(EXIF_BATCH)
            if not batch:
                return n
            for eid, path, size, mtime in batch:
                state, taken = datesmod.read_taken(path)
                self.index.set_exif(eid, size, mtime, EXIF_OK if state == "ok" else state, taken)
                n += 1

    # -------------------------------------------------------------- watchers

    def _restart_watchers(self, force: bool = False) -> None:
        with self._lock:
            if self.watcher_factory is None:
                return
            if self._watchers and not force:
                return
            old, self._watchers = self._watchers, []
            roots = list(self._options["roots"])
            ready = self._started and self._ever_scanned and not self._stopping.is_set()
        for w in old:
            try:
                w.stop()
            except Exception:
                pass
        if not ready:
            return
        new = []
        for i, r in enumerate(roots):
            try:
                new.append(self.watcher_factory(r, i, self._on_watch))
            except Exception:
                pass
        with self._lock:
            self._watchers = new

    def _on_watch(self, root: int, reason: str) -> None:
        if reason != "change":
            with self._lock:
                self._stale_reason = ("Change notifications overflowed; rescanning." if reason == "overflow"
                                      else "Lost contact with a root; rescanning when possible."
                                      if reason.startswith("error") else "A root is reachable again; rescanning.")
            self._publish("stale", root=root, reason=reason.split(":")[0], message=self._stale_reason)
        self._debouncer.poke(root, reason)

    def _on_debounced(self, roots: set[int], reasons: set[str]) -> None:
        if self._stopping.is_set():
            return
        self._request(roots, "change" if reasons == {"change"} else "error")

    def _reconcile_loop(self) -> None:
        while not self._stopping.wait(self.reconcile_s):
            with self._lock:
                ready = self._ever_scanned and bool(self._options["roots"])
            if ready:
                self._request(ALL, "reconcile")

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Load options and arm periodic reconciliation. Scanning starts on the first rescan request (the client
        asks when the File timeline is first shown); watchers start after the first scan."""
        self._stopping.clear()
        self._ensure_loaded()
        with self._lock:
            self._started = True
        if self.reconcile_s and self.reconcile_s > 0:
            self._ensure_thread("reconcile", self._reconcile_loop)
        self._restart_watchers()

    def stop(self) -> None:
        self._stopping.set()
        with self._cond:
            self._cancel.set()
            self._pending = None
            self._started = False
            self._cond.notify_all()
            watchers, self._watchers = self._watchers, []
        self._exif_wake.set()
        self._debouncer.cancel()
        for w in watchers:
            try:
                w.stop()
            except Exception:
                pass
        if self._thumbs is not None:
            self._thumbs.shutdown()
        for t in list(self._threads.values()):
            if t is not threading.current_thread():
                t.join(timeout=2.0)
        self._threads.clear()

    # ---------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            roots = list(self._options["roots"])
            reports = []
            for i, r in enumerate(roots):
                rep = self._reports[i] if i < len(self._reports) else None
                reports.append(rep or {"path": r, "name": root_name(r), "status": "not_scanned", "message": "",
                                       "files": 0, "dirs": 0, "denied": [], "denied_count": 0, "errors": [],
                                       "error_count": 0, "links_skipped": 0, "hidden_skipped": 0, "excluded": 0,
                                       "online_only": 0})
            if not roots:
                state = "no_roots"
            elif self._scanning or self._pending is not None:
                state = "scanning"
            elif not self._ever_scanned:
                state = "never"
            else:
                state = "idle"
            return {
                "state": state,
                "ever_scanned": self._ever_scanned,
                "scanning": self._scanning,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "progress": dict(self._progress),
                "files": self.index.count(),
                "exif_pending": self.index.exif_pending_count(),
                "stale": bool(self._stale_reason),
                "stale_reason": self._stale_reason,
                "partial": self._is_partial(),
                "error": self._last_error,
                "version": self.index.version,
                "roots": reports,
                "options": {"roots": roots, "excluded_ext": list(self._options["excluded_ext"]),
                            "include_hidden": self._options["include_hidden"]},
            }

    # ------------------------------------------------------------------ data

    def entries(self, mode: str) -> dict[str, Any]:
        self._ensure_loaded()
        out = dict(self.index.payload(mode))
        st = self.status()
        out["status"] = {k: st[k] for k in ("state", "files", "exif_pending", "stale", "partial", "version")}
        out["excluded_ext"] = st["options"]["excluded_ext"]
        out["include_hidden"] = st["options"]["include_hidden"]
        return out

    def details(self, eid: str) -> dict[str, Any] | None:
        e = self.index.get(eid)
        if e is None:
            return None
        roots = self.index.roots
        root = roots[e.root] if e.root < len(roots) else ""
        modes = {}
        for mode in datesmod.MODES:
            day, _, src = datesmod.choose(mode, ctime=e.ctime, mtime=e.mtime, taken=e.taken)
            modes[mode] = {"date": day.isoformat() if day else None, "source": src,
                           "source_label": datesmod.SOURCE_LABELS[src], "undated": day is None}
        if e.taken:
            taken = {"value": e.taken.text, "offset": e.taken.offset, "tag": e.taken.tag,
                     "day": e.taken.day.isoformat(), "status": "ok"}
        else:
            msg = {EXIF_PENDING: "Not read yet", "none": "No valid date taken in the file",
                   "unreadable": "Metadata could not be read",
                   "skipped": "Online-only file; not downloaded to read metadata" if e.online_only
                   else "Not applicable"}.get(e.exif_state, "")
            taken = {"value": None, "offset": None, "tag": None, "day": None, "status": e.exif_state, "message": msg}
        try:
            exists = os.path.isfile(e.path)
        except OSError:
            exists = False
        return {
            "id": e.id, "name": e.name, "path": e.path, "folder": os.path.dirname(e.path), "ext": e.ext,
            "kind": "image" if e.is_image else "other", "size": e.size,
            "created": datesmod.fmt_local(e.ctime), "modified": datesmod.fmt_local(e.mtime),
            "taken": taken, "modes": modes,
            "root": {"index": e.root, "path": root, "name": root_name(root) if root else ""},
            "online_only": e.online_only, "exists": exists,
        }

    def _resolve(self, eid: str) -> Any:
        """Re-validate an entry before touching the file: still indexed, inside a current root (also after
        resolving links), still present."""
        e = self.index.get(eid)
        if e is None:
            raise OpenRefused("This file is no longer in the list. Refresh the view.", 404)
        with self._lock:
            roots = list(self._options["roots"])
        root = roots[e.root] if e.root < len(roots) else None
        if root is None or not is_within(e.path, root):
            raise OpenRefused("This file is not inside a current root.", 403)
        try:
            real_path, real_root = os.path.realpath(e.path), os.path.realpath(root)
        except OSError:
            raise OpenRefused("The file cannot be reached right now.", 410)
        if not is_within(real_path, real_root):
            raise OpenRefused("The path now leads outside its root (through a link); it was not opened.", 403)
        try:
            ok = os.path.isfile(e.path)
        except OSError:
            ok = False
        if not ok:
            raise OpenRefused("The file no longer exists or cannot be reached. Refresh the view.", 410)
        return e

    def open(self, eid: str) -> str:
        e = self._resolve(eid)
        self.launcher.open(e.path)
        return e.path

    def reveal(self, eid: str) -> str:
        e = self._resolve(eid)
        self.launcher.reveal(e.path)
        return e.path

    def attachment_path(self, eid: str) -> str:
        return self._resolve(eid).path

    def thumbs(self) -> ThumbService:
        with self._lock:
            if self._thumbs is None:
                self._thumbs = ThumbService(self._cache_dir or (data_dir() / "cache" / "thumbs"))
            return self._thumbs

    def thumb(self, eid: str, gen: int = 0) -> ThumbResult | None:
        """None when the id is unknown or not an indexed image inside a current root."""
        e = self.index.get(eid)
        if e is None or not e.is_image:
            return None
        try:
            e = self._resolve(eid)
        except OpenRefused as r:
            return None if r.status in (403, 404) else ThumbResult("placeholder", reason="inaccessible")
        if e.online_only:
            return ThumbResult("placeholder", reason=REASON_ONLINE_ONLY)
        try:
            st = os.stat(e.path)
        except OSError:
            return ThumbResult("placeholder", reason="inaccessible")
        if (getattr(st, "st_file_attributes", 0) or 0) & CLOUD_ATTRS:
            return ThumbResult("placeholder", reason=REASON_ONLINE_ONLY)
        return self.thumbs().get(e.path, e.key, int(st.st_size), float(st.st_mtime), gen=gen)
