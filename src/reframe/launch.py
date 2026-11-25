"""Process start-up shared by Reframe.pyw and ``python -m reframe`` (R-170, R-174, spec 8.5).

* sets the explicit AppUserModelID before any window exists, so the taskbar shows the Reframe icon and groups the
  window with a pinned shortcut;
* sends diagnostics to ``logs\\reframe.log`` in the data folder (rotating, 5 x 1 MB); there is no console;
* reports a failure that happens before a window exists with one native message box, the only modal Reframe uses.
"""
from __future__ import annotations

import ctypes
import logging
import logging.handlers
import sys
import traceback

from .shortcuts import APP_ID

log = logging.getLogger("reframe")


def set_app_user_model_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:   # not Windows, or an old shell: the generic id is harmless
        pass


class _Redact(logging.Filter):
    """Secret ICS feed addresses never reach the log (R-135)."""

    def filter(self, record: logging.LogRecord) -> bool:
        from .calendars.ics import mask_url
        import re
        msg = record.getMessage()
        masked = re.sub(r"(?:https?|webcals?)://\S+", lambda m: mask_url(m.group(0)), msg)
        if masked != msg:
            record.msg, record.args = masked, ()
        return True


def setup_logging() -> None:
    from .db.connection import data_dir
    folder = data_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / "reframe.log", maxBytes=1_000_000, backupCount=5,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_Redact())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def startup_failure(exc: BaseException) -> None:
    text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    log.error("startup failed: %s\n%s", text, "".join(traceback.format_exception(exc)))
    try:
        ctypes.windll.user32.MessageBoxW(None, f"Reframe could not start.\n\n{text}\n\nDetails are in the log file "
                                         "(the Reframe data folder, logs/reframe.log).", "Reframe", 0x10)
    except Exception:
        print(text, file=sys.stderr)


def run(argv: list[str] | None = None) -> int:
    set_app_user_model_id()
    try:
        setup_logging()
    except Exception:
        pass
    try:
        from .host import main
        return main(argv)
    except SystemExit:
        raise
    except BaseException as e:   # noqa: BLE001 - nothing else can report it without a window
        startup_failure(e)
        return 1
