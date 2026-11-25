"""System tray behaviour (R-104, R-172, R-173): the Reframe icon is present the whole time the app runs.

Clicking it (or Show Reframe) shows and focuses the window. Minimizing hides the window and its taskbar button
when *Minimize to the system tray* is on; closing the window always hides it to the tray; Quit Reframe exits.
``TrayController`` holds the policy and is tested with fakes. ``make_system_icon`` builds the real icon with
pystray; the caller supplies the window operations so this module never imports pywebview.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

TITLE = "Reframe"

# Icon drawing colours (the Meridian navy field and amber accent); the tray is outside the themed web UI (R-91).
_NAVY = (5, 3, 42, 255)
_AMBER = (190, 138, 42, 255)
_SKY = (120, 161, 183, 255)


class TrayIcon(Protocol):
    visible: bool

    def run_detached(self) -> None: ...

    def stop(self) -> None: ...


class TrayController:
    """Decides when the window hides to the tray and when it comes back.

    ``enabled`` (minimize to tray) is read at every minimize so the Settings checkbox takes effect immediately.
    """

    def __init__(self, *, hide_window: Callable[[], None], show_window: Callable[[], None],
                 quit_app: Callable[[], None], sync_now: Callable[[], None], enabled: Callable[[], bool],
                 icon_factory: Callable[["TrayController"], TrayIcon],
                 start_with_windows: Callable[[], bool] = lambda: False,
                 set_start_with_windows: Callable[[bool], None] = lambda on: None,
                 notify: Callable[[str], None] = lambda text: None):
        self._hide_window, self._show_window = hide_window, show_window
        self._quit, self.sync_now = quit_app, sync_now
        self._enabled = enabled
        self._icon_factory = icon_factory
        self.start_with_windows, self._set_start = start_with_windows, set_start_with_windows
        self._notify = notify
        self._icon: TrayIcon | None = None
        self.in_tray = False
        self.quitting = False

    def start(self) -> None:
        """Create the icon at startup: it is always present while Reframe runs (R-104)."""
        if self._icon is None:
            self._icon = self._icon_factory(self)
            self._icon.run_detached()          # pystray's default setup makes the icon visible

    def on_minimized(self) -> None:
        if self.in_tray or not self._enabled():
            return
        self.start()
        self.in_tray = True
        self._hide_window()

    def on_closing(self, first_time: bool = False) -> bool:
        """Closing the window hides it to the tray instead of exiting. Returns True to let the close proceed."""
        if self.quitting:
            return True
        self.start()
        self.in_tray = True
        self._hide_window()
        if first_time:
            self._notify("Reframe is still running in the tray. Use Quit to exit.")
        return False

    def on_window_visible(self) -> None:
        """The window was restored or shown by any route (tray click, second launch, taskbar)."""
        self.in_tray = False

    def restore(self) -> None:
        self.in_tray = False
        self._show_window()

    def toggle_start_with_windows(self) -> None:
        self._set_start(not self.start_with_windows())

    def quit(self) -> None:
        self.quitting = True
        self.shutdown()
        self._quit()

    def shutdown(self) -> None:
        if self._icon is not None:
            try:
                self._icon.visible = False
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        self.in_tray = False


def draw_icon_image(size: int = 64) -> Any:
    """A small timeline glyph: three staggered bars on a navy tile."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 16
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=3 * u, fill=_NAVY)
    d.rounded_rectangle([2 * u, 3.5 * u, 9 * u, 6 * u], radius=u, fill=_AMBER)
    d.rounded_rectangle([5 * u, 7 * u, 14 * u, 9.5 * u], radius=u, fill=_SKY)
    d.rounded_rectangle([3 * u, 10.5 * u, 11 * u, 13 * u], radius=u, fill=_AMBER)
    return img


def make_system_icon(controller: TrayController) -> TrayIcon:
    import pystray
    menu = pystray.Menu(
        pystray.MenuItem(f"Show {TITLE}", lambda *_: controller.restore(), default=True),
        pystray.MenuItem("Sync now", lambda *_: controller.sync_now()),
        pystray.MenuItem("Start with Windows", lambda *_: controller.toggle_start_with_windows(),
                         checked=lambda _item: controller.start_with_windows()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Quit {TITLE}", lambda *_: controller.quit()),
    )
    icon = pystray.Icon(TITLE, draw_icon_image(), TITLE, menu)
    icon.visible = False
    return icon
