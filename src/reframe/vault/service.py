"""Background vault service: startup recovery, reconciliation, watchers, capture queue (R-160 - R-166).

Usage (host)::

    svc = app.config["VAULT"]      # created by server.api_vault.init_app
    svc.start()                    # after the launch backup
    ...
    svc.stop()                     # on Quit, before the exit backup

All file I/O runs on a bounded pool (2 workers); each task opens its own short-lived DB connection.
"""
from __future__ import annotations

import concurrent.futures as cf
import contextlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from ..db.connection import connect
from . import capture as cap
from . import materialize, reconcile
from .store import VaultStore, default_vault_root, is_synced_folder
from .watch import KeyedDebouncer, WatchSet


class _Gate:
    """Shared/exclusive gate: captures share it; prune and move take it exclusively."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._shared = 0
        self._excl = False

    @contextlib.contextmanager
    def shared(self) -> Iterator[None]:
        with self._cond:
            while self._excl:
                self._cond.wait()
            self._shared += 1
        try:
            yield
        finally:
            with self._cond:
                self._shared -= 1
                self._cond.notify_all()

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        with self._cond:
            while self._excl:
                self._cond.wait()
            self._excl = True
            while self._shared:
                self._cond.wait()
        try:
            yield
        finally:
            with self._cond:
                self._excl = False
                self._cond.notify_all()


class VaultService:
    def __init__(self, db_path: str | os.PathLike, hub: Any = None, store_root: str | os.PathLike | None = None, *,
                 workers: int = 2, debounce_s: float = 2.0, reconcile_interval_s: float = 900.0,
                 retry_base_s: float = 10.0, watch: bool = True,
                 watcher_factory: Callable | None = None):
        self.db_path = str(db_path)
        self.hub = hub
        self._fixed_root = Path(store_root) if store_root else None
        self._store: VaultStore | None = None
        self.workers = workers
        self.debounce_s = debounce_s
        self.reconcile_interval_s = reconcile_interval_s
        self.retry_base_s = retry_base_s
        self.watch_enabled = watch
        self._watcher_factory = watcher_factory
        self._pool: cf.ThreadPoolExecutor | None = None
        self._pool_lock = threading.Lock()
        self._halt = threading.Event()
        self._running = False
        self._gate = _Gate()
        self._path_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._outstanding = 0
        self._idle = threading.Condition()
        self._retries: dict[str, int] = {}
        self._timers: set[threading.Timer] = set()
        self._watch: WatchSet | None = None
        self._debounce: KeyedDebouncer | None = None
        self._periodic: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_reconcile_at: str | None = None
        self.last_startup: dict[str, Any] | None = None

    # ------------------------------------------------------------- store
    @property
    def store(self) -> VaultStore:
        if self._store is None:
            self._store = VaultStore(self._resolve_root())
        return self._store

    def _resolve_root(self) -> Path:
        if self._fixed_root is not None:
            return self._fixed_root
        conn = connect(self.db_path)
        try:
            return default_vault_root(conn)
        finally:
            conn.close()

    def refresh_store(self) -> VaultStore:
        root = self._resolve_root()
        if self._store is None or Path(self._store.root) != root:
            self._store = VaultStore(root)
        return self._store

    # --------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._running:
            return
        self._halt.clear()
        self._running = True
        self._ensure_pool()
        if self.watch_enabled:
            self._debounce = KeyedDebouncer(self._on_debounced, quiet_s=self.debounce_s)
            self._watch = WatchSet(self._on_watch_event, self._watcher_factory)
        self.submit(self._startup)
        if self.reconcile_interval_s and self.reconcile_interval_s > 0:
            self._periodic = threading.Thread(target=self._periodic_loop, daemon=True, name="vault-periodic")
            self._periodic.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop watchers and workers, then remove temporary copies (R-162). Safe to call twice."""
        self._halt.set()
        self._running = False
        for t in list(self._timers):
            t.cancel()
        self._timers.clear()
        if self._debounce:
            self._debounce.stop()
        if self._watch:
            self._watch.stop_all()
        if self._periodic:
            self._periodic.join(timeout=2)
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            deadline = time.monotonic() + timeout
            for t in list(getattr(pool, "_threads", ())):
                t.join(max(0.0, deadline - time.monotonic()))
        materialize.cleanup_tmp()

    @property
    def running(self) -> bool:
        return self._running

    def _ensure_pool(self) -> cf.ThreadPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = cf.ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="vault")
            return self._pool

    def submit(self, fn: Callable, *args: Any) -> cf.Future:
        with self._idle:
            self._outstanding += 1

        def run():
            try:
                return fn(*args)
            except Exception as e:          # never kill the worker; report
                self.last_error = f"{type(e).__name__}: {e}"
                self._publish()
                raise
            finally:
                with self._idle:
                    self._outstanding -= 1
                    self._idle.notify_all()
        try:
            return self._ensure_pool().submit(run)
        except RuntimeError:
            with self._idle:
                self._outstanding -= 1
                self._idle.notify_all()
            raise

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until no task is queued or running (tests, Quit)."""
        end = time.monotonic() + timeout
        with self._idle:
            while self._outstanding:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._idle.wait(left)
        return True

    # ------------------------------------------------------------ public
    def notify_attachment_added(self, attachment_id: str) -> cf.Future:
        """Capture and pin a newly added file link right away (called by the API after the link is committed)."""
        return self.submit(self._protect_link, attachment_id)

    def reconcile_now(self, deep: bool = False) -> cf.Future:
        return self.submit(self._reconcile, deep)

    def status(self, conn: sqlite3.Connection | None = None, *, detail: bool = False) -> dict[str, Any]:
        own = conn is None
        conn = conn or connect(self.db_path)
        try:
            c = reconcile.counts(conn)
            store = self.store
            out: dict[str, Any] = {
                "running": self._running, "queued": self._outstanding, "pending": c["pending"],
                "protected": c["protected"], "last_error": self.last_error,
                "last_reconcile_at": self.last_reconcile_at, "vault_path": str(store.root),
                "synced_folder_warning": (
                    "The vault is inside a OneDrive/synced folder. Choose a local folder (Settings > Attachments)."
                    if is_synced_folder(store.root) else None)}
            if detail:
                out["usage"] = store.size_info(conn)
                out["unprotected"] = reconcile.unprotected(conn)
            return out
        finally:
            if own:
                conn.close()

    # ------------------------------------------------------------- tasks
    @contextlib.contextmanager
    def _path_lock(self, key: str) -> Iterator[None]:
        with self._locks_lock:
            lock = self._path_locks.setdefault(key, threading.Lock())
        with lock:
            yield

    def _startup(self) -> dict[str, Any]:
        store = self.refresh_store()
        store.ensure()
        removed = store.recover()
        tmp = materialize.cleanup_tmp()
        self.last_startup = {"staging_removed": removed, "tmp_removed": tmp}
        self._reconcile(True)
        return self.last_startup

    def _reconcile(self, deep: bool) -> dict[str, Any]:
        store = self.refresh_store()
        conn = connect(self.db_path)
        try:
            files = reconcile.attached_files(conn)
            migrated_at = reconcile.migration_time(conn)
            summary = {"checked": 0, "captured": 0, "pinned": 0, "failed": []}
            for key, entry in files.items():
                if self._halt.is_set():
                    break
                r = self._reconcile_one(conn, store, key, entry, deep, migrated_at)
                summary["checked"] += 1
                summary["captured"] += int(bool(r["captured"]))
                summary["pinned"] += len(r["pinned"])
                if r["status"] not in (cap.PROTECTED, "unchanged"):
                    summary["failed"].append({"path": entry["path"], "status": r["status"], "error": r["error"]})
            self.last_reconcile_at = cap.now_iso()
            self._update_watches(files)
            self._publish(conn)
            return summary
        finally:
            conn.close()

    def _reconcile_one(self, conn, store, key, entry, deep, migrated_at) -> dict[str, Any]:
        with self._gate.shared(), self._path_lock(key):
            r = reconcile.reconcile_path(conn, store, entry["path"], entry["links"], deep=deep,
                                         migrated_at=migrated_at)
        self._after_attempt(key, r["status"], r["error"], entry["path"])
        return r

    def _protect_link(self, attachment_id: str) -> dict[str, Any] | None:
        conn = connect(self.db_path)
        try:
            a = conn.execute("SELECT a.*, i.deleted_at FROM attachment a JOIN item i ON i.id = a.item_id "
                             "WHERE a.id=?", (attachment_id,)).fetchone()
            if a is None or a["kind"] != "file" or a["pinned_version_id"] or a["deleted_at"]:
                return None
            target = cap.strip_long(a["target"].strip().strip('"'))
            key = cap.path_key(target)
            files = reconcile.attached_files(conn)
            entry = files.get(key, {"path": target, "links": [dict(a)]})
            r = self._reconcile_one(conn, self.store, key, entry, True, reconcile.migration_time(conn))
            self._update_watches(files)
            self._publish(conn)
            return r
        finally:
            conn.close()

    def _capture_key(self, key: str) -> None:
        conn = connect(self.db_path)
        try:
            entry = reconcile.attached_files(conn).get(key)
            if entry is None:
                return
            self._reconcile_one(conn, self.store, key, entry, True, reconcile.migration_time(conn))
            self._publish(conn)
        finally:
            conn.close()

    def _after_attempt(self, key: str, status: str | None, error: str | None, path: str) -> None:
        if status in (cap.PROTECTED, "unchanged"):
            self._retries.pop(key, None)
            return
        self.last_error = f"{os.path.basename(path)}: {error or status}"
        if status in cap.RETRYABLE and not self._halt.is_set():
            n = self._retries.get(key, 0)
            self._retries[key] = n + 1
            delay = min(self.retry_base_s * (2 ** n), 900.0)
            if status == "changed":
                delay = min(delay, max(self.debounce_s, 1.0) * (n + 1))
            t = threading.Timer(delay, self._retry, (key,))
            t.daemon = True
            self._timers.add(t)
            t.start()

    def _retry(self, key: str) -> None:
        self._timers = {t for t in self._timers if t.is_alive()}
        if not self._halt.is_set():
            try:
                self.submit(self._capture_key, key)
            except RuntimeError:
                pass

    # ----------------------------------------------------------- watching
    def _update_watches(self, files: dict[str, dict]) -> None:
        if self._watch is None or self._halt.is_set():
            return
        self._watch.update(e["path"] for e in files.values())

    def _on_watch_event(self, directory: str, name: str | None, reason: str) -> None:
        if self._debounce is None or self._halt.is_set():
            return
        if name:
            self._debounce.poke(cap.path_key(os.path.join(directory, name)))
            return
        dkey = os.path.normcase(os.path.abspath(directory))   # overflow / error / recovered: check the folder's files
        conn = connect(self.db_path)
        try:
            for key, e in reconcile.attached_files(conn).items():
                if os.path.normcase(os.path.dirname(os.path.abspath(e["path"]))) == dkey:
                    self._debounce.poke(key)
        finally:
            conn.close()

    def _on_debounced(self, key: str) -> None:
        if not self._halt.is_set():
            self.submit(self._capture_key, key)

    def _periodic_loop(self) -> None:
        while not self._halt.wait(self.reconcile_interval_s):
            try:
                self.reconcile_now(deep=False)
            except RuntimeError:
                break

    # ------------------------------------------------------------ events
    def _publish(self, conn: sqlite3.Connection | None = None) -> None:
        if self.hub is None:
            return
        try:
            s = self.status(conn)
            self.hub.publish("vault", {k: s[k] for k in ("pending", "protected", "queued", "last_error",
                                                         "last_reconcile_at")})
        except Exception:
            pass

    # ---------------------------------------------------- storage control
    def prune(self, confirm: bool = False) -> dict[str, Any]:
        """R-165: versions referenced by no link (incl. soft-deleted items) and no change-log snapshot, plus
        orphan blobs. ``confirm=False`` only reports count and size."""
        with self._gate.exclusive():
            store = self.refresh_store()
            conn = connect(self.db_path)
            try:
                rows = conn.execute(
                    "SELECT v.id, v.sha256, v.size FROM file_version v "
                    "WHERE NOT EXISTS (SELECT 1 FROM attachment a WHERE a.pinned_version_id = v.id) "
                    "AND NOT EXISTS (SELECT 1 FROM change c WHERE c.table_name = 'attachment' AND "
                    "(instr(c.before_json, v.id) > 0 OR instr(c.after_json, v.id) > 0))").fetchall()
                ids = [r["id"] for r in rows]
                keep = {r[0] for r in conn.execute(
                    f"SELECT DISTINCT sha256 FROM file_version WHERE id NOT IN "
                    f"(SELECT value FROM json_each(?))", (_json(ids),))}
                doomed = {s for s in store.iter_shas() if s not in keep}
                size = sum(_blob_size(store, s) for s in doomed)
                out = {"versions": len(ids), "blobs": len(doomed), "bytes": size,
                       "logical_bytes": sum(r["size"] for r in rows), "deleted": False}
                if not confirm:
                    return out
                with cap._tx(conn):
                    conn.execute("DELETE FROM file_version WHERE id IN (SELECT value FROM json_each(?))", (_json(ids),))
                keep = {r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM file_version")}
                freed = sum(store.delete(s) for s in doomed if s not in keep)
                out.update(deleted=True, bytes=freed)
                self._publish(conn)
                return out
            finally:
                conn.close()

    def move(self, dest: str | os.PathLike) -> dict[str, Any]:
        """R-166: copy + verify all content into ``dest``, then switch the ``vault_dir`` setting. The old folder
        is kept until the owner removes it."""
        from ..domain import settings as settingsmod
        new_root = Path(str(dest).strip().strip('"'))
        if not new_root.is_absolute():
            raise ValueError("Choose a full folder path for the vault")
        with self._gate.exclusive():
            old = self.refresh_store()
            if os.path.normcase(os.path.abspath(new_root)) == os.path.normcase(os.path.abspath(old.root)):
                raise ValueError("That is already the vault folder")
            new = VaultStore(new_root)
            new.ensure()
            copied = total = 0
            for sha in list(old.iter_shas()):
                total += 1
                copied += int(new.import_blob(old, sha))
            conn = connect(self.db_path)
            try:
                needed = [r[0] for r in conn.execute("SELECT DISTINCT sha256 FROM file_version")]
                missing = [s for s in needed if not new.verify(s)]
                if any(old.has(s) for s in missing):
                    raise ValueError(f"Copy verification failed for {len(missing)} version(s); vault not moved")
                if self._fixed_root is None:
                    settingsmod.set_many(conn, {"vault_dir": str(new_root)})
                else:
                    self._fixed_root = new_root
                self._store = new
            finally:
                conn.close()
        return {"old_path": str(old.root), "new_path": str(new_root), "blobs": total, "copied": copied,
                "missing_before_move": missing,
                "synced_folder_warning": is_synced_folder(new_root)}


def _json(v: Any) -> str:
    import json
    return json.dumps(v)


def _blob_size(store: VaultStore, sha: str) -> int:
    try:
        return store.blob_path(sha).stat().st_size
    except OSError:
        return 0
