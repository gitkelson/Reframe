"""Windows shortcuts for Reframe (R-171, R-173, R-174).

Each shortcut is named ``Reframe.lnk``, runs ``pythonw.exe "<app>\\Reframe.pyw"`` (Startup adds ``--tray``),
carries the Reframe icon and the AppUserModelID ``Reframe.Timeline`` so a pinned taskbar button and the running
window group together. Only shortcuts this module created (same name and a target pointing at our launcher) are
ever replaced or removed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "Reframe.Timeline"
NAME = "Reframe.lnk"
APP_DIR = Path(__file__).resolve().parents[2]
LAUNCHER = APP_DIR / "Reframe.pyw"
ICON = APP_DIR / "assets" / "reframe.ico"


def pythonw() -> Path:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def _programs() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def locations() -> dict[str, Path]:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    try:
        from win32com.shell import shell, shellcon
        desktop = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    except Exception:
        pass
    return {"start-menu": _programs() / NAME, "desktop": desktop / NAME, "startup": _programs() / "Startup" / NAME}


def _link():
    import pythoncom
    from win32com.shell import shell
    return pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)


def read(path: Path) -> tuple[str, str] | None:
    """(target, arguments) of an existing shortcut, or None."""
    if not path.exists():
        return None
    import pythoncom
    from win32com.shell import shell
    link = _link()
    link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path), 0)
    target = link.GetPath(shell.SLGP_RAWPATH)[0]
    return target, link.GetArguments()


def is_ours(path: Path) -> bool:
    found = read(path)
    return bool(found) and "reframe.pyw" in found[1].lower() and "pythonw" in found[0].lower()


def create(path: Path, *, tray: bool = False) -> Path:
    import pythoncom
    from win32com.propsys import propsys, pscon
    pythoncom.CoInitialize()
    if path.exists() and not is_ours(path):
        raise FileExistsError(f"{path} exists and was not created by Reframe; leaving it alone")
    path.parent.mkdir(parents=True, exist_ok=True)
    link = _link()
    link.SetPath(str(pythonw()))
    link.SetArguments(f'"{LAUNCHER}"' + (" --tray" if tray else ""))
    link.SetWorkingDirectory(str(APP_DIR))
    link.SetDescription("Reframe timeline")
    if ICON.exists():
        link.SetIconLocation(str(ICON), 0)
    store = link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID))
    store.Commit()
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)
    return path


def remove(path: Path) -> bool:
    if path.exists() and is_ours(path):
        path.unlink()
        return True
    return False


def set_start_with_windows(on: bool) -> None:
    """Maintain the Startup shortcut that launches Reframe hidden in the tray (R-173)."""
    path = locations()["startup"]
    if on:
        create(path, tray=True)
    else:
        remove(path)


def start_with_windows_enabled() -> bool:
    try:
        return is_ours(locations()["startup"])
    except Exception:
        return False
