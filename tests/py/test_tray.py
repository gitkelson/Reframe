from reframe.tray import TrayController, draw_icon_image


class FakeIcon:
    def __init__(self):
        self.visible = False
        self.running = False
        self.stopped = False

    def run_detached(self):
        self.running = True
        self.visible = True      # pystray's default setup shows the icon


def make(enabled=True, startup=None):
    log = []
    icons = []
    startup = startup if startup is not None else [False]

    def factory(ctrl):
        icons.append(FakeIcon())
        icons[-1].stop = lambda i=icons[-1]: setattr(i, "stopped", True)
        return icons[-1]

    ctrl = TrayController(
        hide_window=lambda: log.append("hide"), show_window=lambda: log.append("show"),
        quit_app=lambda: log.append("quit"), sync_now=lambda: log.append("sync"),
        enabled=lambda: enabled[0] if isinstance(enabled, list) else enabled, icon_factory=factory,
        start_with_windows=lambda: startup[0], set_start_with_windows=lambda on: startup.__setitem__(0, on),
        notify=lambda text: log.append(f"notice:{text}"))
    return ctrl, log, icons


def test_icon_is_present_from_start_and_stays_while_the_window_is_shown():
    ctrl, log, icons = make()
    ctrl.start()
    assert icons[0].running and icons[0].visible and log == []
    ctrl.on_minimized()
    ctrl.restore()
    assert icons[0].visible and len(icons) == 1 and log == ["hide", "show"]


def test_minimize_does_nothing_when_disabled_and_setting_is_read_each_time():
    flag = [False]
    ctrl, log, icons = make(flag)
    ctrl.start()
    ctrl.on_minimized()
    assert log == [] and not ctrl.in_tray
    flag[0] = True
    ctrl.on_minimized()
    assert log == ["hide"]


def test_repeated_minimize_events_hide_once():
    ctrl, log, icons = make()
    ctrl.on_minimized()
    ctrl.on_minimized()
    assert log == ["hide"] and len(icons) == 1


def test_closing_hides_to_tray_and_first_close_explains_quit():
    ctrl, log, icons = make()
    ctrl.start()
    assert ctrl.on_closing(first_time=True) is False                    # the close is cancelled
    assert log == ["hide", "notice:Reframe is still running in the tray. Use Quit to exit."]
    assert ctrl.on_closing(first_time=False) is False and log.count("hide") == 2
    assert icons[0].visible


def test_closing_is_allowed_while_quitting():
    ctrl, log, icons = make()
    ctrl.start()
    ctrl.quit()
    assert ctrl.on_closing() is True


def test_window_shown_by_another_route_leaves_tray_mode():
    ctrl, log, icons = make()
    ctrl.on_minimized()
    ctrl.on_window_visible()              # second launch or taskbar restored the window
    assert not ctrl.in_tray and icons[0].visible
    ctrl.on_minimized()                   # and it can collapse again; the same icon is reused
    assert len(icons) == 1 and log == ["hide", "hide"]


def test_quit_stops_icon_then_quits_app():
    ctrl, log, icons = make()
    ctrl.start()
    ctrl.quit()
    assert icons[0].stopped and log[-1] == "quit"


def test_start_with_windows_menu_toggle():
    state = [False]
    ctrl, log, icons = make(startup=state)
    ctrl.toggle_start_with_windows()
    assert state == [True] and ctrl.start_with_windows()
    ctrl.toggle_start_with_windows()
    assert state == [False]


def test_icon_image_is_a_square_rgba_tile():
    img = draw_icon_image(64)
    assert img.size == (64, 64) and img.mode == "RGBA"
    assert img.getpixel((32, 4)) != (0, 0, 0, 0)       # painted, not blank
    assert img.getpixel((0, 0))[3] == 0                 # rounded corner stays transparent
