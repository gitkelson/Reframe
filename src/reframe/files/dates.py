"""Date sources for the File timeline (R-144).

Modes: 'modified' places every file by its filesystem modification time. 'created' places an image by its date
taken (EXIF DateTimeOriginal, else DateTimeDigitized), or an MP4 by Windows' Media created property, when valid.
Otherwise the filesystem creation time is used. EXIF times are wall-clock values; an explicit offset is converted
to the local day. Windows Media created is UTC and is converted to the local day.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

SOURCE_EXIF, SOURCE_CREATED, SOURCE_MODIFIED, SOURCE_MEDIA = "exif", "created", "modified", "media"
SOURCE_LABELS = {
    SOURCE_EXIF: "Date taken (EXIF)",
    SOURCE_CREATED: "File created",
    SOURCE_MODIFIED: "File modified",
    SOURCE_MEDIA: "Media created",
}
SOURCE_CODES = {SOURCE_EXIF: 0, SOURCE_CREATED: 1, SOURCE_MODIFIED: 2, SOURCE_MEDIA: 3}
MODES = ("created", "modified")

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".bmp", ".dib", ".tif", ".tiff",
                        ".webp", ".ico", ".heic", ".heif", ".avif"})
EXIF_EXTS = frozenset({".jpg", ".jpeg", ".jpe", ".jfif", ".tif", ".tiff", ".webp", ".png", ".heic", ".heif",
                       ".avif"})

TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_OFFSET_TIME = 0x9010
TAG_OFFSET_ORIGINAL = 0x9011
TAG_OFFSET_DIGITIZED = 0x9012
EXIF_IFD = 0x8769

MIN_VALID_TS = 86400.0           # an unset FILETIME/epoch is not a date
MIN_YEAR, MAX_YEAR = 1800, 2200
EPOCH_ORDINAL = dt.date(1970, 1, 1).toordinal()

_EXIF_RE = re.compile(r"^\s*(\d{4})[:\-/](\d{2})[:\-/](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_OFFSET_RE = re.compile(r"^\s*([+-])(\d{2}):?(\d{2})\s*$")


@dataclass(frozen=True)
class TakenDate:
    text: str                    # wall-clock value as stored, 'YYYY-MM-DD HH:MM:SS'
    offset: str | None           # '+02:00' when an offset tag was present
    tag: str                     # metadata property name
    day: dt.date                 # placement day
    sort_ts: float               # ordering key (seconds)
    source: str = SOURCE_EXIF


def day_number(d: dt.date) -> int:
    """Days since 1970-01-01, the same day numbers the web client uses (dates.js)."""
    return d.toordinal() - EPOCH_ORDINAL


def valid_ts(ts: Any) -> bool:
    return isinstance(ts, (int, float)) and math.isfinite(ts) and ts >= MIN_VALID_TS


def local_datetime(ts: Any) -> dt.datetime | None:
    if not valid_ts(ts):
        return None
    try:
        d = dt.datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None
    return d if MIN_YEAR <= d.year <= MAX_YEAR else None


def local_day(ts: Any) -> dt.date | None:
    d = local_datetime(ts)
    return d.date() if d else None


def _text(v: Any) -> str | None:
    if isinstance(v, bytes):
        try:
            v = v.decode("ascii", "strict")
        except UnicodeDecodeError:
            return None
    if not isinstance(v, str):
        return None
    return v.replace("\x00", "").strip() or None


def parse_exif_datetime(v: Any) -> dt.datetime | None:
    s = _text(v)
    if not s:
        return None
    m = _EXIF_RE.match(s)
    if not m:
        return None
    y, mo, d, hh, mi, ss = (int(g) for g in m.groups())
    if not (MIN_YEAR <= y <= MAX_YEAR):
        return None
    try:
        return dt.datetime(y, mo, d, hh, mi, min(ss, 59))
    except ValueError:
        return None


def parse_offset(v: Any) -> dt.timezone | None:
    s = _text(v)
    if not s:
        return None
    m = _OFFSET_RE.match(s)
    if not m:
        return None
    sign, hh, mi = m.groups()
    minutes = int(hh) * 60 + int(mi)
    if minutes > 14 * 60 or int(mi) >= 60:
        return None
    return dt.timezone(dt.timedelta(minutes=-minutes if sign == "-" else minutes))


def _fmt_offset(tz: dt.timezone) -> str:
    total = int(tz.utcoffset(None).total_seconds() // 60)
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 60:02d}:{total % 60:02d}"


def taken_from_exif(tags: Mapping[int, Any]) -> TakenDate | None:
    """Date taken from merged IFD0 + Exif IFD tags, or None when absent or invalid (caller falls back)."""
    for tag, off_tag, name in ((TAG_DATETIME_ORIGINAL, TAG_OFFSET_ORIGINAL, "DateTimeOriginal"),
                               (TAG_DATETIME_DIGITIZED, TAG_OFFSET_DIGITIZED, "DateTimeDigitized")):
        naive = parse_exif_datetime(tags.get(tag))
        if naive is None:
            continue
        tz = parse_offset(tags.get(off_tag)) or parse_offset(tags.get(TAG_OFFSET_TIME))
        text = naive.strftime("%Y-%m-%d %H:%M:%S")
        if tz is not None:
            aware = naive.replace(tzinfo=tz)
            try:
                local = aware.astimezone()
                day = local.date()
            except (OverflowError, OSError, ValueError):
                day = naive.date()
            return TakenDate(text, _fmt_offset(tz), name, day, aware.timestamp())
        try:
            sort_ts = naive.timestamp()        # ordering only; the placement day is the wall-clock date
        except (OverflowError, OSError, ValueError):
            sort_ts = (naive - dt.datetime(1970, 1, 1)).total_seconds()
        return TakenDate(text, None, name, naive.date(), sort_ts)
    return None


def read_exif_taken(path: str) -> tuple[str, TakenDate | None]:
    """('ok', TakenDate) | ('none', None) when absent/invalid | ('unreadable', None) when the file cannot be parsed.

    Only the header is parsed (``Image.open`` is lazy); pixels are never decoded here.
    """
    from PIL import Image

    try:
        with Image.open(path) as im:
            exif = im.getexif()
            merged: dict[int, Any] = dict(exif)
            try:
                merged.update(exif.get_ifd(EXIF_IFD))
            except Exception:
                pass
    except Exception:
        return "unreadable", None
    t = taken_from_exif(merged)
    return ("ok", t) if t else ("none", None)


def _read_media_created(path: str) -> dt.datetime | None:
    import pythoncom
    import pywintypes
    from win32com.propsys import propsys, pscon

    pythoncom.CoInitialize()
    try:
        try:
            store = propsys.SHGetPropertyStoreFromParsingName(path, None, 0, propsys.IID_IPropertyStore)
            try:
                return store.GetValue(pscon.PKEY_Media_DateEncoded).GetValue()
            finally:
                del store
        except pywintypes.com_error as exc:
            raise OSError(f"Cannot read media metadata: {exc}") from exc
    finally:
        pythoncom.CoUninitialize()


def read_media_taken(path: str) -> tuple[str, TakenDate | None]:
    try:
        value = _read_media_created(path)
    except OSError:
        return "unreadable", None
    if not isinstance(value, dt.datetime):
        return "none", None
    try:
        if value.utcoffset() is None:
            return "none", None
        utc = value.astimezone(dt.timezone.utc)
        if not MIN_YEAR <= utc.year <= MAX_YEAR or utc == dt.datetime(1904, 1, 1, tzinfo=dt.timezone.utc):
            return "none", None
        local = utc.astimezone()
        taken = TakenDate(utc.strftime("%Y-%m-%d %H:%M:%S"), "+00:00", "Media created",
                          local.date(), utc.timestamp(), SOURCE_MEDIA)
    except (OverflowError, OSError, ValueError):
        return "none", None
    return "ok", taken


def read_taken(path: str) -> tuple[str, TakenDate | None]:
    return read_media_taken(path) if os.path.splitext(path)[1].lower() == ".mp4" else read_exif_taken(path)


def choose(mode: str, *, ctime: Any, mtime: Any, taken: TakenDate | None) -> tuple[dt.date | None, float | None, str]:
    """(placement day or None when undated, sort timestamp, source) for one file in a date mode."""
    if mode == "modified":
        d = local_day(mtime)
        return (d, float(mtime), SOURCE_MODIFIED) if d else (None, None, SOURCE_MODIFIED)
    if taken is not None:
        return taken.day, taken.sort_ts, taken.source
    d = local_day(ctime)
    return (d, float(ctime), SOURCE_CREATED) if d else (None, None, SOURCE_CREATED)


def fmt_local(ts: Any) -> str | None:
    d = local_datetime(ts)
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else None
