"""Server-Sent Events hub and the database watcher (R-85, spec 10.2)."""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Iterator

from ..db.changelog import group_external, reset_stale_context
from ..db.connection import connect


class Hub:
    """Fan-out of events to connected SSE clients."""

    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        msg = (event_type, data or {})
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass  # a stalled client resyncs when it reconnects

    def stream(self, keepalive_s: float = 15.0, stop: threading.Event | None = None) -> Iterator[str]:
        q = self.subscribe()
        try:
            yield "retry: 1000\n\nevent: hello\ndata: {}\n\n"
            while not (stop and stop.is_set()):
                try:
                    et, data = q.get(timeout=keepalive_s)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {et}\ndata: {json.dumps(data)}\n\n"
        finally:
            self.unsubscribe(q)


class Watcher(threading.Thread):
    """Polls ``PRAGMA data_version`` and pushes a 'data' event when another connection wrote.

    Also folds writes that skipped the change-log protocol into one 'external' change set.
    """

    def __init__(self, db_path: str, hub: Hub, interval_s: float = 1.0):
        super().__init__(daemon=True, name="reframe-watcher")
        self.db_path, self.hub, self.interval = db_path, hub, interval_s
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        conn = connect(self.db_path)
        try:
            last = conn.execute("PRAGMA data_version").fetchone()[0]
            last_change = conn.execute("SELECT coalesce(max(id), 0) FROM change").fetchone()[0]
            while not self._halt.wait(self.interval):
                try:
                    cur = conn.execute("PRAGMA data_version").fetchone()[0]
                    if cur == last:
                        continue
                    reset_stale_context(conn)
                    ext_set = group_external(conn)
                    ids, last_change = changed_item_ids(conn, last_change)
                    last = conn.execute("PRAGMA data_version").fetchone()[0]
                    self.hub.publish("data", {"external": bool(ext_set), "change_set_id": ext_set,
                                              "item_ids": ids})
                except Exception:
                    time.sleep(self.interval)  # transient (busy, file replaced); try again
        finally:
            conn.close()


def changed_item_ids(conn, after_change_id: int) -> tuple[list[str], int]:
    """Item ids touched by change rows newer than ``after_change_id`` and the new high-water mark."""
    ids: set[str] = set()
    high = after_change_id
    for r in conn.execute("SELECT id, table_name, row_key, after_json, before_json FROM change WHERE id > ?",
                          (after_change_id,)):
        high = max(high, r["id"])
        if r["table_name"] == "item":
            ids.add(r["row_key"])
        elif r["table_name"] == "item_tag":
            ids.add(r["row_key"].split("|")[0])
        elif r["table_name"] == "attachment":
            try:
                ids.add(json.loads(r["after_json"] or r["before_json"])["item_id"])
            except (TypeError, ValueError, KeyError):
                pass
    return sorted(ids), high
