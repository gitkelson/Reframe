"""Launches the Flask server thread, the tray icon and the native pywebview window (spec 8.1, 10.2).

One process: Flask runs on a background thread bound to 127.0.0.1 on an ephemeral port; the window runs on the
main thread; the tray icon on its own thread and is present the whole time Reframe runs (R-104). Closing the
window hides it to the tray; Quit (tray, View menu, Ctrl+Q) runs the exit sequence.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from werkzeug.serving import make_server

from . import shortcuts
from .db.connection import connect, data_dir, default_db_path, init_db
from .domain import backup, settings
from .domain import tags as tagmod
from .launch import set_app_user_model_id
from .server.app import ICON_FILE, create_app, start_watcher
from .tray import TrayController, make_system_icon

WINDOW_TITLE = "Reframe"
log = logging.getLogger("reframe.host")


class SingleInstance:
    """Lock file in the data folder (R-102). Only one running copy holds it."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is not None:
            import msvcrt
            try:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self._fh.close()
            self._fh = None


def find_window() -> int:
    """Handle of the running Reframe window, ignoring shell proxy windows that share its title."""
    try:
        import win32gui
    except ImportError:
        return 0
    found: list[tuple[int, int]] = []

    def visit(hwnd: int, _: Any) -> None:
        if win32gui.GetWindowText(hwnd) != WINDOW_TITLE:
            return
        cls = win32gui.GetClassName(hwnd)
        if cls.startswith("WindowsForms") or cls.startswith("Chrome_WidgetWin"):   # the pywebview form
            found.append((0, hwnd))
        elif "Proxy" not in cls:
            found.append((1, hwnd))

    win32gui.EnumWindows(visit, None)
    return min(found)[1] if found else 0


def focus_existing_window() -> bool:
    """Bring the already-running Reframe window to the front, also when it is hidden in the tray (R-102)."""
    try:
        import win32con
        import win32gui
    except ImportError:
        return False
    hwnd = find_window()
    if not hwnd:
        return False
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    return True


def backup_policy(conn, *, reason: str) -> str | None:
    """Backup on launch / exit (spec 8.4). A failure is reported but never blocks use."""
    folder = Path(settings.get(conn, "backup_folder") or backup.default_backup_dir())
    try:
        path = backup.create_backup(conn, folder)
        backup.apply_retention(folder, keep_last=settings.get(conn, "backup_keep_last"),
                               keep_days=settings.get(conn, "backup_keep_days"),
                               keep_months=settings.get(conn, "backup_keep_months"))
        return str(path)
    except OSError as e:
        log.warning("%s backup failed: %s", reason, e)
        return None


class HostBridge:
    """Native services the page cannot do itself: file choosers, Explorer, Quit and the Startup shortcut."""

    def __init__(self) -> None:
        self.window: Any = None
        self.quit_cb: Any = None

    def _dialog(self, kind: str, **kw) -> str | None:
        import webview
        if self.window is None:
            return None
        enum = getattr(webview, "FileDialog", None)
        mode = (getattr(enum, kind.upper()) if enum is not None else getattr(webview, f"{kind.upper()}_DIALOG"))
        res = self.window.create_file_dialog(mode, **kw)
        if isinstance(res, str):
            return res
        return res[0] if res else None

    def choose_folder(self) -> str | None:
        return self._dialog("folder")

    def choose_file(self) -> str | None:
        return self._dialog("open")

    def choose_save(self, name: str = "") -> str | None:
        return self._dialog("save", save_filename=name or "")

    def reveal(self, path: str) -> None:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def quit(self) -> None:
        if self.quit_cb:
            threading.Thread(target=self.quit_cb, daemon=True, name="reframe-quit").start()

    def set_start_with_windows(self, on: bool) -> None:
        try:
            shortcuts.set_start_with_windows(on)
        except Exception as e:
            log.warning("Start with Windows shortcut not updated: %s", e)


def build_server(db_path: Path, *, port: int = 0, host: Any = None):
    app = create_app(db_path, host=host)
    server = make_server("127.0.0.1", port, app, threaded=True)
    app.config["PORT"] = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="reframe-http")
    thread.start()
    start_watcher(app)
    return app, server


def start_services(app, conn) -> None:
    """Background work after the window exists (spec 8.1): sync, attachment protection, File timeline, hygiene."""
    sync = app.config["SYNC"]
    if settings.get(conn, "sync_on_launch"):
        sync.start()
    sync.start_scheduler()
    for key in ("VAULT", "FILES"):
        svc = app.config.get(key)
        if svc is not None and hasattr(svc, "start"):
            try:
                svc.start()
            except Exception as e:   # one failing service never stops the app
                log.error("%s service did not start: %s", key, e)
    drift = tagmod.implied_drift(conn)
    if drift["missing"] or drift["extra"]:
        log.info("tag hygiene: %d missing and %d extra implied tags (repair offered in the tag manager)",
                 len(drift["missing"]), len(drift["extra"]))


def stop_services(app) -> None:
    for key in ("FILES", "VAULT"):
        svc = app.config.get(key)
        if svc is not None and hasattr(svc, "stop"):
            try:
                svc.stop()
            except Exception:
                pass
    app.config["SYNC"].stop()
    app.config["WATCHER"].stop()


def _enable_text_field_menu(window) -> None:
    """R-190: pywebview disables WebView2's default context menus outside debug mode, which removes the spelling
    suggestions while the spell checker still underlines words. Re-enable them; the page suppresses the menu
    everywhere except text fields (context_menu.js)."""
    try:
        from System import Action  # type: ignore[import-not-found]
        form = window.native

        def enable() -> None:
            form.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
        form.Invoke(Action(enable))
    except Exception as e:
        log.warning("could not enable the text-field context menu: %s", e)


def _set_window_icon(window) -> None:
    """The window and taskbar button use the Reframe icon, never the generic Python one (R-174)."""
    try:
        from System import Action  # type: ignore[import-not-found]
        from System.Drawing import Icon  # type: ignore[import-not-found]
        form = window.native

        def apply() -> None:
            form.Icon = Icon(str(ICON_FILE))
        form.Invoke(Action(apply))
    except Exception as e:
        log.warning("could not set the window icon: %s", e)


def main(argv: list[str] | None = None) -> int:
    set_app_user_model_id()
    ap = argparse.ArgumentParser(prog="reframe", description="Reframe timeline")
    ap.add_argument("--db", help="database file (default: reframe.sqlite in the data folder)")
    ap.add_argument("--no-window", action="store_true", help="serve only and print the URL (development)")
    ap.add_argument("--tray", action="store_true", help="start hidden with only the tray icon (Start with Windows)")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--debug", action="store_true", help="allow developer tools in the window")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else default_db_path()
    lock = SingleInstance((db_path.parent if args.db else data_dir()) / "reframe.lock")
    if not lock.acquire():
        focus_existing_window()
        return 0
    conn = init_db(db_path)
    launch_backup = backup_policy(conn, reason="launch")

    bridge = HostBridge()
    app, server = build_server(db_path, port=args.port, host=None if args.no_window else bridge)
    if launch_backup is None:
        app.config["BACKUP_ERROR"] = "The backup on launch failed. Check the backup folder in Settings."
    url = f"http://127.0.0.1:{app.config['PORT']}/?t={app.config['TOKEN']}"
    stopped = threading.Event()

    def shutdown() -> None:
        if stopped.is_set():
            return
        stopped.set()
        stop_services(app)
        server.shutdown()
        backup_policy(conn, reason="exit")
        try:
            from .vault import materialize
            materialize.cleanup_tmp()
        except Exception:
            pass
        conn.close()
        lock.release()

    try:
        if args.no_window:
            print(url, flush=True)
            if os.environ.get("REFRAME_START_SERVICES") == "1":
                start_services(app, conn)
            threading.Event().wait()
            return 0
        import webview
        geo = settings.get(conn, "window_geometry") or {}
        window = webview.create_window(
            WINDOW_TITLE, url, width=int(geo.get("width", 1400)), height=int(geo.get("height", 900)),
            x=geo.get("x"), y=geo.get("y"), min_size=(900, 600), text_select=False, hidden=args.tray)
        bridge.window = window

        def read_setting(key: str) -> Any:
            c = connect(db_path)
            try:
                return settings.get(c, key)
            finally:
                c.close()

        def first_close() -> bool:
            if read_setting("tray_close_notice_shown"):
                return False
            c = connect(db_path)
            try:
                settings.set_many(c, {"tray_close_notice_shown": True})
            finally:
                c.close()
            return True

        def notify(text: str) -> None:
            app.config["HUB"].publish("notice", {"text": text, "kind": "info", "ttl": 8000})
            try:
                if tray._icon is not None and hasattr(tray._icon, "notify"):
                    tray._icon.notify(text, WINDOW_TITLE)
            except Exception:
                pass

        def set_start(on: bool) -> None:
            bridge.set_start_with_windows(on)
            c = connect(db_path)
            try:
                settings.set_many(c, {"start_with_windows": bool(on)})
            finally:
                c.close()

        tray = TrayController(
            hide_window=lambda: threading.Thread(target=window.hide, daemon=True).start(), show_window=lambda: (window.show(), window.restore(), focus_existing_window()),
            quit_app=window.destroy, sync_now=app.config["SYNC"].start,
            enabled=lambda: bool(read_setting("tray_on_minimize")), icon_factory=make_system_icon,
            start_with_windows=shortcuts.start_with_windows_enabled, set_start_with_windows=set_start, notify=notify)
        bridge.quit_cb = tray.quit
        tray.start()
        # keep the Startup shortcut in step with the setting (R-173)
        if read_setting("start_with_windows") != shortcuts.start_with_windows_enabled():
            bridge.set_start_with_windows(bool(read_setting("start_with_windows")))

        def save_geometry() -> None:
            if tray.in_tray or window.x is None or window.x < -10000 or window.width < 200:
                return   # a hidden or minimized window never saves its geometry
            try:
                settings.set_many(conn, {"window_geometry": {
                    "x": window.x, "y": window.y, "width": window.width, "height": window.height}})
            except Exception:
                pass

        def on_closing() -> bool:
            save_geometry()
            return tray.on_closing(first_time=first_close() if not tray.quitting else False)

        def on_loaded() -> None:
            _enable_text_field_menu(window)

        def on_shown() -> None:
            tray.on_window_visible()

        window.events.minimized += tray.on_minimized
        window.events.restored += tray.on_window_visible
        window.events.before_show += lambda: _set_window_icon(window)
        window.events.shown += on_shown
        window.events.loaded += on_loaded
        window.events.closing += on_closing
        threading.Thread(target=start_services, args=(app, connect(db_path)), daemon=True,
                         name="reframe-services").start()
        try:
            webview.start(debug=args.debug, icon=str(ICON_FILE))
        finally:
            tray.shutdown()
        return 0
    finally:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())
