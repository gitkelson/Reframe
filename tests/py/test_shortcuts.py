"""Shortcuts (R-171, R-173, R-174), exercised in a temp folder only: never the real Start menu or Startup."""
import pytest

pytest.importorskip("win32com")

from reframe import shortcuts


def test_create_carries_launcher_icon_and_app_id_and_remove_only_touches_ours(tmp_path):
    from win32com.propsys import propsys, pscon
    link = tmp_path / "Reframe.lnk"
    shortcuts.create(link, tray=True)
    target, args = shortcuts.read(link)
    assert target.lower().endswith("pythonw.exe") and "Reframe.pyw" in args and args.endswith("--tray")
    store = propsys.SHGetPropertyStoreFromParsingName(str(link), None, 0, propsys.IID_IPropertyStore)
    assert store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue() == shortcuts.APP_ID
    del store                                               # releases the file handle before remove()
    assert shortcuts.is_ours(link)
    shortcuts.create(link)                                  # idempotent repair
    assert shortcuts.read(link)[1].endswith('Reframe.pyw"')
    assert shortcuts.remove(link) and not link.exists()

    foreign = tmp_path / "Other" / "Reframe.lnk"
    foreign.parent.mkdir()
    import pythoncom
    from win32com.shell import shell
    other = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
    other.SetPath(r"C:\Windows\notepad.exe")
    other.QueryInterface(pythoncom.IID_IPersistFile).Save(str(foreign), 0)
    assert not shortcuts.is_ours(foreign)
    assert not shortcuts.remove(foreign) and foreign.exists()
    with pytest.raises(FileExistsError):
        shortcuts.create(foreign)
