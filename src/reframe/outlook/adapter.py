"""Adapter interface for reading Outlook calendars (spec 3.6) and the classic-Outlook COM implementation.

The sync logic (sync.py) only sees ``RawEvent`` records, so it is tested with
``fake_adapter.FakeAdapter`` and runs against real Outlook through ``ComAdapter``.
Read-only: nothing here writes to Outlook.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol

OL_FOLDER_CALENDAR = 9
OL_APPOINTMENT_ITEM = 1
OL_MODULE_CALENDAR = 1
OL_EXCHANGE_PUBLIC_FOLDER = 2     # public folders are out of scope and never walked
MEETING_CANCELED = (5, 7)         # olMeetingCanceled, olMeetingReceivedAndCanceled
RECURRENCE_NONE, RECURRENCE_MASTER, RECURRENCE_OCCURRENCE, RECURRENCE_EXCEPTION = 0, 1, 2, 3
DEFAULT_DATE_FORMAT = "%m/%d/%Y %I:%M %p"


class OutlookUnavailable(Exception):
    """Outlook is not installed, not running, blocked, or the new Outlook is in use (R-68)."""


@dataclass
class CalendarInfo:
    id: str
    name: str
    path: str
    item_count: int | None = None
    is_default: bool = False
    looks_like_holidays: bool = False


@dataclass
class RawEvent:
    calendar_id: str
    entry_id: str
    gaid: str                      # GlobalAppointmentID; shared by every occurrence of a series
    subject: str
    location: str
    start: dt.datetime             # naive local wall-clock time (tzinfo stripped, R-64)
    end: dt.datetime
    all_day: bool
    recurring: bool = False        # an occurrence or exception of a recurring series
    cancelled: bool = False
    original_date: dt.date | None = None   # pre-move date of an exception occurrence (R-63)


@dataclass
class CalendarRead:
    events: list[RawEvent] = field(default_factory=list)
    complete: bool = True          # False if the read failed part-way: no removals are applied (R-65)
    error: str | None = None


class Adapter(Protocol):
    def list_calendars(self) -> list[CalendarInfo]: ...

    def read_calendar(self, calendar_id: str, lookback_start: dt.date, horizon_end: dt.date) -> CalendarRead: ...


def naive_local(value) -> dt.datetime | None:
    """Wall-clock datetime with tzinfo stripped.

    pywin32 attaches a misleading tzinfo label (Phase 0: always "UTC+00:00 London")
    to values that are already local wall-clock time. Never call astimezone().
    """
    if value is None:
        return None
    return dt.datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


class ComAdapter:
    """Classic Outlook via COM. Use as a context manager on the thread that will read.

    COM is initialised on the calling thread (``pythoncom.CoInitialize``).
    """

    def __init__(self, date_format: str = DEFAULT_DATE_FORMAT):
        self.date_format = date_format
        self._app = None
        self._ns = None
        self._folders: dict[str, object] = {}
        self._com_initialised = False

    def __enter__(self) -> "ComAdapter":
        try:
            import pythoncom
            import win32com.client
        except ImportError as e:
            raise OutlookUnavailable("pywin32 is not installed") from e
        pythoncom.CoInitialize()
        self._com_initialised = True
        try:
            try:
                self._app = win32com.client.GetActiveObject("Outlook.Application")
            except Exception:
                self._app = win32com.client.Dispatch("Outlook.Application")
            self._ns = self._app.GetNamespace("MAPI")
        except Exception as e:
            self.__exit__(None, None, None)
            raise OutlookUnavailable(
                "Cannot reach classic Outlook through COM. Start classic Outlook (the 'new Outlook' toggle "
                f"does not support COM). Detail: {e}") from e
        return self

    def __exit__(self, *exc) -> None:
        self._app = self._ns = None
        self._folders.clear()
        if self._com_initialised:
            import pythoncom
            pythoncom.CoUninitialize()
            self._com_initialised = False

    # ------------------------------------------------------------ calendars
    def list_calendars(self) -> list[CalendarInfo]:
        found: dict[str, tuple[object, str]] = {}
        default_id = None

        def add(folder) -> None:
            try:
                key = folder.EntryID
            except Exception:
                return
            found.setdefault(key, (folder, ""))

        try:
            default = self._ns.GetDefaultFolder(OL_FOLDER_CALENDAR)
            default_id = default.EntryID
            add(default)
        except Exception:
            pass

        def walk(folder, depth: int) -> None:
            if depth > 3:
                return
            try:
                subs = folder.Folders
                for i in range(1, subs.Count + 1):
                    try:
                        sub = subs.Item(i)
                        if sub.DefaultItemType == OL_APPOINTMENT_ITEM:
                            add(sub)
                        walk(sub, depth + 1)
                    except Exception:
                        continue
            except Exception:
                return

        try:
            for store in self._ns.Stores:
                try:
                    if store.ExchangeStoreType == OL_EXCHANGE_PUBLIC_FOLDER:
                        continue
                    walk(store.GetRootFolder(), 0)
                except Exception:
                    continue
        except Exception:
            pass

        try:  # navigation pane lists shared, group and holiday calendars
            explorer = self._app.ActiveExplorer()
            if explorer is not None:
                module = explorer.NavigationPane.Modules.GetNavigationModule(OL_MODULE_CALENDAR)
                for group in module.NavigationGroups:
                    for nav in group.NavigationFolders:
                        try:
                            add(nav.Folder)
                        except Exception:
                            continue
        except Exception:
            pass

        out: list[CalendarInfo] = []
        for key, (folder, _) in found.items():
            try:
                name, path = folder.Name, folder.FolderPath
            except Exception:
                continue
            if "\\" not in (path or ""):
                # group and shared calendars report an opaque id as their path; show store and name instead
                try:
                    path = f"\\\\{folder.Store.DisplayName}\\{name}"
                except Exception:
                    path = f"\\\\(group or shared calendar)\\{name}"
            try:
                count = folder.Items.Count
            except Exception:
                count = None
            self._folders[key] = folder
            out.append(CalendarInfo(id=key, name=name, path=path, item_count=count, is_default=key == default_id,
                                    looks_like_holidays="holiday" in (name or "").lower()))
        return out

    def _folder(self, calendar_id: str):
        if calendar_id not in self._folders:
            try:
                self._folders[calendar_id] = self._ns.GetFolderFromID(calendar_id)
            except Exception as e:
                raise OutlookUnavailable(f"Calendar not found: {e}") from e
        return self._folders[calendar_id]

    # ----------------------------------------------------------------- read
    def read_calendar(self, calendar_id: str, lookback_start: dt.date, horizon_end: dt.date) -> CalendarRead:
        """Non-recurring items without an upper bound; recurring series expanded over the window (R-62)."""
        try:
            folder = self._folder(calendar_id)
        except OutlookUnavailable as e:
            return CalendarRead(complete=False, error=str(e))
        out = CalendarRead()
        a = dt.datetime.combine(lookback_start, dt.time.min)
        b = dt.datetime.combine(horizon_end, dt.time.max)
        fmt = self.date_format
        try:
            # 1) non-recurring items, no upper date bound
            items = folder.Items
            items.IncludeRecurrences = False
            flt = f"[IsRecurring] = False AND [End] >= '{a.strftime(fmt)}'"
            self._collect(items.Restrict(flt), calendar_id, out, recurring=False)
            # 2) recurring series expanded into occurrences over the window
            items = folder.Items
            items.Sort("[Start]")
            items.IncludeRecurrences = True
            flt = f"[Start] <= '{b.strftime(fmt)}' AND [End] >= '{a.strftime(fmt)}'"
            self._collect(items.Restrict(flt), calendar_id, out, recurring=True)
        except Exception as e:
            out.complete = False
            out.error = f"Outlook read failed: {e}"
        return out

    def _collect(self, restricted, calendar_id: str, out: CalendarRead, *, recurring: bool) -> None:
        item = restricted.GetFirst()
        while item is not None:
            try:
                state = item.RecurrenceState
                is_rec = state != RECURRENCE_NONE
                if is_rec != recurring or state == RECURRENCE_MASTER:
                    item = restricted.GetNext()
                    continue
                start, end = naive_local(item.Start), naive_local(item.End)
                if start is None or end is None:
                    item = restricted.GetNext()
                    continue
                ev = RawEvent(
                    calendar_id=calendar_id,
                    entry_id=_safe(item, "EntryID", ""),
                    gaid=_safe(item, "GlobalAppointmentID", "") or "",
                    subject=_safe(item, "Subject", "") or "",
                    location=_safe(item, "Location", "") or "",
                    start=start, end=end,
                    all_day=bool(_safe(item, "AllDayEvent", False)),
                    recurring=is_rec,
                    cancelled=_safe(item, "MeetingStatus", 0) in MEETING_CANCELED,
                )
                if state == RECURRENCE_EXCEPTION:
                    ev.original_date = _original_date_of_exception(item, start, end)
                out.events.append(ev)
            except Exception as e:  # one bad item must not abort the whole read
                out.error = out.error or f"Skipped an unreadable item: {e}"
            item = restricted.GetNext()


def _safe(obj, name: str, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _original_date_of_exception(item, start: dt.datetime, end: dt.datetime) -> dt.date | None:
    """Pre-move date of an exception, matched through RecurrencePattern.Exceptions (Phase 0: 3 of 3)."""
    try:
        exceptions = item.GetRecurrencePattern().Exceptions
        for i in range(1, exceptions.Count + 1):
            ex = exceptions.Item(i)
            if _safe(ex, "Deleted", False):
                continue
            try:
                ex_item = ex.AppointmentItem
                if naive_local(ex_item.Start) == start and naive_local(ex_item.End) == end:
                    return naive_local(ex.OriginalDate).date()
            except Exception:
                continue
    except Exception:
        return None
    return None
