"""Runs calendar sync on background threads (R-67, R-68, R-134).

Outlook (COM initialised on its own thread) and ICS sources sync independently; ICS URL feeds are refreshed on
their interval by a small scheduler. Status, per-source results and progress are pushed as 'sync' events.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from ..calendars import ics_sync
from ..calendars.sync import source_message
from ..db.connection import connect
from ..domain import backup, settings
from ..outlook import sync as sync_mod
from ..outlook.adapter import ComAdapter, OutlookUnavailable
from .events import Hub

KINDS = ("outlook", "ics")
SCHEDULE_CHECK_S = 60


def default_adapter_factory(conn):
    return ComAdapter(settings.get(conn, "outlook_date_format") or "%m/%d/%Y %I:%M %p")


class SyncManager:
    def __init__(self, db_path: str, hub: Hub, adapter_factory: Callable[[Any], Any] = default_adapter_factory,
                 ics_reader: Callable[..., Any] | None = None):
        self.db_path, self.hub, self.adapter_factory = db_path, hub, adapter_factory
        self.ics_reader = ics_reader
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._halt = threading.Event()
        self._scheduler: threading.Thread | None = None
        self.kinds: dict[str, dict[str, Any]] = {
            k: {"running": False, "stage": "", "done": 0, "total": 0, "last_result": None, "error": None}
            for k in KINDS}

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        running = [k for k, s in self.kinds.items() if s["running"]]
        stage = " · ".join(self.kinds[k]["stage"] for k in running if self.kinds[k]["stage"])
        results = [s["last_result"] for s in self.kinds.values() if s["last_result"]]
        errors = [s["error"] for s in self.kinds.values() if s["error"]]
        sources = [c for r in results for c in r.get("calendars", [])]
        message = " · ".join(source_message(c) for c in sources)
        last = None
        if results:
            ids = [r.get("change_set_id") for r in results if r.get("change_set_id")]
            last = {"message": message or "up to date", "change_set_id": max(ids) if ids else None,
                    "warnings": [w for r in results for w in r.get("warnings", [])],
                    "status": "partial" if any(r.get("status") == "partial" for r in results) else "ok",
                    "added": sum(r.get("added", 0) for r in results),
                    "updated": sum(r.get("updated", 0) for r in results),
                    "removed": sum(r.get("removed", 0) for r in results),
                    "new_sources": sum(r.get("new_sources", 0) for r in results)}
        return {"running": bool(running), "stage": stage, "done": 0, "total": 0, "last_result": last,
                "error": "; ".join(errors) or None, "sources": sources,
                "kinds": {k: {kk: v for kk, v in s.items() if kk != "last_result"} for k, s in self.kinds.items()}}

    def _publish(self) -> None:
        self.hub.publish("sync", self.status())

    # ------------------------------------------------------------------- start

    def start(self, kinds: tuple[str, ...] = KINDS, *, ics_sources: list[str] | None = None) -> bool:
        """Start a sync of the given kinds unless one of that kind is running. True if anything started."""
        started = False
        with self._lock:
            for k in kinds:
                st = self.kinds[k]
                if st["running"]:
                    continue
                st.update(running=True, stage="Starting", error=None)
                target = self._run_outlook if k == "outlook" else (lambda ids=ics_sources: self._run_ics(ids))
                t = threading.Thread(target=target, daemon=True, name=f"reframe-sync-{k}")
                self._threads[k] = t
                t.start()
                started = True
        if started:
            self._publish()
        return started

    def start_scheduler(self) -> None:
        """Refresh ICS URL feeds whose interval has passed (R-134)."""
        if self._scheduler is not None:
            return

        def loop() -> None:
            while not self._halt.wait(SCHEDULE_CHECK_S):
                try:
                    conn = connect(self.db_path)
                    try:
                        due = [s["id"] for s in ics_sync.due_sources(conn)]
                    finally:
                        conn.close()
                    if due:
                        self.start(("ics",), ics_sources=due)
                except Exception:
                    pass

        self._scheduler = threading.Thread(target=loop, daemon=True, name="reframe-sync-schedule")
        self._scheduler.start()

    def stop(self) -> None:
        self._halt.set()

    def join(self, timeout: float | None = None) -> None:
        for t in list(self._threads.values()):
            t.join(timeout)

    # ------------------------------------------------------------------ workers

    def _progress(self, kind: str) -> Callable[[str, int, int], None]:
        def report(stage: str, done: int, total: int) -> None:
            self.kinds[kind].update(stage=stage, done=done, total=total)
            self._publish()
        return report

    def _backup(self, conn) -> None:
        folder = Path(settings.get(conn, "backup_folder") or backup.default_backup_dir())
        try:
            backup.create_backup(conn, folder)
        except OSError as e:
            self.hub.publish("backup", {"error": f"Pre-sync backup failed: {e}. Check the backup folder in Settings."})

    def _finish(self, kind: str, result: dict[str, Any] | None, error: str | None) -> None:
        self.kinds[kind].update(running=False, stage="", last_result=result, error=error)
        self._publish()

    def _run_outlook(self) -> None:
        conn = connect(self.db_path)
        result, error = None, None
        try:
            with self.adapter_factory(conn) as adapter:
                result = sync_mod.run_sync(conn, adapter, progress=self._progress("outlook"),
                                           backup_cb=lambda: self._backup(conn))
            for c in result.get("calendars", []):
                c["name"] = f"Outlook {c['name']}"
        except OutlookUnavailable as e:
            error = str(e)
        except Exception as e:  # a COM failure ends the run with a message and leaves data intact (spec 8.5)
            error = f"Outlook sync failed: {e}"
        finally:
            conn.close()
            self._finish("outlook", result, error)

    def _run_ics(self, source_ids: list[str] | None) -> None:
        conn = connect(self.db_path)
        result, error = None, None
        try:
            from ..calendars import sources as sourcemod
            if not any(s["connected"] for s in sourcemod.list_sources(conn, kind="ics", public=False)):
                return
            self._backup(conn)
            kw = {"reader": self.ics_reader} if self.ics_reader else {}
            result = ics_sync.run_ics_sync(conn, source_ids=source_ids, progress=self._progress("ics"), **kw)
            if result["status"] == "no_calendars":
                result = None
        except Exception as e:
            error = f"Calendar feed sync failed: {e}"
        finally:
            conn.close()
            self._finish("ics", result, error)
