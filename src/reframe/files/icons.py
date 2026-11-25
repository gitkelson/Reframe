"""Windows file-association icons, without opening the indexed files."""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageOps


@lru_cache(maxsize=256)
def file_type_icon(extension: str) -> bytes:
    """Return a PNG for an extension using the current Windows file association."""
    import pythoncom
    import pywintypes

    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            return _read_icon(extension)
        finally:
            pythoncom.CoUninitialize()
    except (pywintypes.error, pywintypes.com_error) as exc:
        raise OSError(f"Windows icon lookup failed: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise OSError(f"Invalid Windows icon bitmap: {exc}") from exc


def _read_icon(extension: str) -> bytes:
    import win32con
    import win32gui
    import win32ui
    from win32com.shell import shell, shellcon

    flags = shellcon.SHGFI_ICON | shellcon.SHGFI_LARGEICON | shellcon.SHGFI_USEFILEATTRIBUTES
    _, info = shell.SHGetFileInfo("file" + extension, win32con.FILE_ATTRIBUTE_NORMAL, flags)
    icon = info[0]
    if not icon:
        raise OSError("Windows did not return a file type icon")
    mask = color = None
    try:
        _, _, _, mask, color = win32gui.GetIconInfo(icon)
        if not color:
            raise OSError("Windows returned a monochrome icon without a color bitmap")
        bitmap = win32ui.CreateBitmapFromHandle(color)
        bitmap_info = bitmap.GetInfo()
        width, height = bitmap_info["bmWidth"], bitmap_info["bmHeight"]
        bits, stride = bitmap_info["bmBitsPixel"], bitmap_info["bmWidthBytes"]
        raw = bitmap.GetBitmapBits(True)
        if bits == 32:
            image = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA", stride, 1)
        elif bits == 24:
            image = Image.frombytes("RGB", (width, height), raw, "raw", "BGR", stride, 1).convert("RGBA")
            image.putalpha(0)
        else:
            raise OSError(f"Unsupported Windows icon bitmap depth: {bits}")
        if image.getchannel("A").getextrema() == (0, 0):
            if not mask:
                raise OSError("Windows icon has no transparency mask")
            mask_bitmap = win32ui.CreateBitmapFromHandle(mask)
            mask_info = mask_bitmap.GetInfo()
            mask_image = Image.frombytes("1", (width, height), mask_bitmap.GetBitmapBits(True),
                                         "raw", "1", mask_info["bmWidthBytes"], 1)
            image.putalpha(ImageOps.invert(mask_image.convert("L")))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()
    finally:
        if mask:
            win32gui.DeleteObject(mask)
        if color:
            win32gui.DeleteObject(color)
        win32gui.DestroyIcon(icon)
