"""ICS calendar files and feeds (R-135..R-138). Pure: no database access.

``read_source`` reads a file path or URL and returns the occurrences that the sync
engine upserts. Recurring series are expanded over [win_start, win_end] only;
non-recurring events are kept when they end on or after ``win_start`` (no upper
bound, R-62). Any read or parse problem yields ``complete=False`` so the caller
applies no removals (R-138). Feed URLs are treated as secrets: error texts only
ever contain the masked form (``mask_url``).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import icalendar
import recurring_ical_events

NO_SUBJECT = "(no subject)"
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://")


@dataclass
class IcsOccurrence:
    uid: str
    weak_key: bool
    original_start: str
    recurring: bool
    title: str
    location: str | None
    start_date: str
    end_date: str
    time_text: str | None
    all_day: bool
    cancelled: bool


@dataclass
class IcsRead:
    events: list[IcsOccurrence] = field(default_factory=list)
    complete: bool = True
    error: str | None = None
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None


# --------------------------------------------------------------------------- locations

def normalize_location(text: str) -> tuple[str, str]:
    """Return ('file'|'url', normalized location). Raises ValueError if unusable."""
    s = (text or "").strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    if not s:
        raise ValueError("empty calendar location")
    m = _SCHEME_RE.match(s)
    if m:
        scheme = m.group(1).lower()
        rest = s[m.end():]
        if scheme in ("webcal", "webcals"):
            scheme = "https"
        if scheme in ("http", "https"):
            if not urlsplit(f"{scheme}://{rest}").hostname:
                raise ValueError("URL has no host")
            return "url", f"{scheme}://{rest}"
        if scheme == "file":
            parts = urlsplit(s)
            path = urllib.request.url2pathname(parts.path)
            if parts.netloc and parts.netloc.lower() != "localhost":
                path = f"\\\\{parts.netloc}{path}"
            if not path:
                raise ValueError("file URL has no path")
            return "file", os.path.normpath(os.path.abspath(path))
        raise ValueError(f"unsupported scheme '{scheme}'")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]+:", s) and not re.match(r"^[A-Za-z]:", s):
        raise ValueError("unsupported location")
    return "file", os.path.normpath(os.path.abspath(os.path.expanduser(s)))


def is_plain_http(url: str) -> bool:
    return (url or "").strip().strip("\"'").strip().lower().startswith("http://")


def mask_url(url: str) -> str:
    """'https://host/…' for URLs (path and query elided); file paths unchanged."""
    if not url or not _SCHEME_RE.match(url.strip()):
        return url
    parts = urlsplit(url.strip())
    if parts.scheme.lower() == "file":
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    tail = parts.path not in ("", "/") or parts.query or parts.fragment
    return f"{parts.scheme.lower()}://{host}/…" if tail else f"{parts.scheme.lower()}://{host}"


# --------------------------------------------------------------------------- parsing

def _local_naive(value: dt.datetime, tz: dt.tzinfo | None) -> dt.datetime:
    if value.tzinfo is None:
        return value  # floating: local wall-clock
    return (value.astimezone(tz) if tz is not None else value.astimezone()).replace(tzinfo=None)


def _when(comp, tz) -> tuple[dt.date | dt.datetime, dt.date | dt.datetime, bool]:
    """(start, end, all_day) with timed values as local naive datetimes."""
    start_prop = comp.get("DTSTART")
    if start_prop is None:
        raise ValueError("event without DTSTART")
    start = start_prop.dt
    if not isinstance(start, (dt.date, dt.datetime)):
        raise ValueError("unreadable DTSTART")
    end = None
    if comp.get("DTEND") is not None:
        end = comp["DTEND"].dt
    elif comp.get("DURATION") is not None:
        dur = comp["DURATION"].dt
        if isinstance(dur, dt.timedelta):
            end = start + dur
    all_day = not isinstance(start, dt.datetime)
    if all_day:
        if isinstance(end, dt.datetime):
            end = end.date()
        if not isinstance(end, dt.date) or end <= start:
            end = start + dt.timedelta(days=1)
        return start, end, True
    start = _local_naive(start, tz)
    if isinstance(end, dt.datetime):
        end = _local_naive(end, tz)
    elif isinstance(end, dt.date):
        end = dt.datetime.combine(end, dt.time())
    else:
        end = start
    return start, end, False


def _dates(start, end, all_day: bool) -> tuple[dt.date, dt.date]:
    if all_day:
        s, e = start, end - dt.timedelta(days=1)
    else:
        s = start.date()
        e = (end - dt.timedelta(seconds=1)).date() if end > start else s
    return s, max(s, e)


def _iso(value, tz) -> str:
    if isinstance(value, dt.datetime):
        return _local_naive(value, tz).replace(microsecond=0).isoformat()
    return value.isoformat()


def _prop_text(comp, name: str) -> str:
    v = comp.get(name)
    if v is None:
        return ""
    try:
        return v.to_ical().decode("utf-8", "replace") if hasattr(v, "to_ical") else str(v)
    except Exception:
        return str(v)


def _weak_uid(comp) -> str:
    key = "\x1f".join((str(comp.get("SUMMARY", "")).strip(), _prop_text(comp, "DTSTART"), _prop_text(comp, "DTEND")))
    return "nouid:" + hashlib.sha1(key.encode("utf-8")).hexdigest()


def _occurrence(comp, uid: str, weak: bool, recurring: bool, tz) -> IcsOccurrence:
    start, end, all_day = _when(comp, tz)
    s, e = _dates(start, end, all_day)
    rid = comp.get("RECURRENCE-ID")
    original = _iso(rid.dt, tz) if rid is not None else _iso(comp["DTSTART"].dt, tz)
    if rid is not None and all_day and "T" in original:
        original = original[:10]
    title = str(comp.get("SUMMARY", "") or "").strip() or NO_SUBJECT
    loc = str(comp.get("LOCATION", "") or "").strip() or None
    return IcsOccurrence(
        uid=uid, weak_key=weak, original_start=original, recurring=recurring,
        title=title, location=loc, start_date=s.isoformat(), end_date=e.isoformat(),
        time_text=None if all_day else f"{start:%H:%M}-{end:%H:%M}",
        all_day=all_day,
        cancelled=str(comp.get("STATUS", "")).strip().upper() == "CANCELLED",
    )


def parse_and_expand(data: bytes, win_start: dt.date, win_end: dt.date, *,
                     tz: dt.tzinfo | None = None) -> IcsRead:
    """Parse ICS bytes into occurrences. Never raises."""
    try:
        text = data.decode("utf-8-sig") if isinstance(data, bytes) else str(data)
        if "BEGIN:VCALENDAR" not in text.upper() or "END:VCALENDAR" not in text.upper():
            return IcsRead(events=[], complete=False, error="not a complete iCalendar file")
        cal = icalendar.Calendar.from_ical(text)
    except Exception as exc:  # noqa: BLE001 - any parse failure means "incomplete"
        return IcsRead(events=[], complete=False, error=f"could not parse calendar: {type(exc).__name__}")

    errors: list[str] = []
    weak: set[str] = set()
    groups: dict[str, list] = {}
    try:
        vevents = list(cal.walk("VEVENT"))
        for comp in vevents:
            uid = str(comp.get("UID", "") or "").strip()
            if not uid:
                uid = _weak_uid(comp)
                weak.add(uid)
                if "UID" in comp:
                    del comp["UID"]
                comp.add("UID", uid)
            groups.setdefault(uid, []).append(comp)
    except Exception as exc:  # noqa: BLE001
        return IcsRead(events=[], complete=False, error=f"could not read events: {type(exc).__name__}")

    def is_rec(comps) -> bool:
        return any(c.get("RRULE") is not None or c.get("RDATE") is not None
                   or c.get("RECURRENCE-ID") is not None for c in comps)

    recurring_uids = {u for u, comps in groups.items() if is_rec(comps)}
    events: list[IcsOccurrence] = []

    for uid, comps in groups.items():
        if uid in recurring_uids:
            continue
        for comp in comps:
            try:
                occ = _occurrence(comp, uid, uid in weak, False, tz)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"skipped an unreadable event ({exc})")
                continue
            if occ.end_date >= win_start.isoformat():
                events.append(occ)

    if recurring_uids:
        try:
            expanded = recurring_ical_events.of(cal).between(
                win_start - dt.timedelta(days=2), win_end + dt.timedelta(days=2))
        except Exception as exc:  # noqa: BLE001
            return IcsRead(events=[], complete=False,
                           error=f"could not expand recurring events: {type(exc).__name__}")
        seen: set[tuple[str, str]] = set()
        for comp in expanded:
            uid = str(comp.get("UID", "") or "").strip()
            if uid not in recurring_uids:
                continue
            try:
                occ = _occurrence(comp, uid, uid in weak, True, tz)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"skipped an unreadable occurrence ({exc})")
                continue
            if occ.end_date < win_start.isoformat() or occ.start_date > win_end.isoformat():
                continue
            if (uid, occ.original_start) in seen:
                continue
            seen.add((uid, occ.original_start))
            events.append(occ)

    if errors:
        return IcsRead(events=events, complete=False,
                       error=f"{len(errors)} event(s) could not be read; first: {errors[0]}")
    return IcsRead(events=events)


# --------------------------------------------------------------------------- reading

def _scrub(msg: str, url: str) -> str:
    masked = mask_url(url)
    for secret in sorted({url, urlsplit(url).path, urlsplit(url).query}, key=len, reverse=True):
        if secret and len(secret) > 1:
            msg = msg.replace(secret, masked if secret == url else "…")
    return msg


def fetch(url: str, *, etag: str | None = None, last_modified: str | None = None,
          timeout: float = 30.0) -> tuple[bytes | None, IcsRead]:
    """GET a feed with conditional headers. Never raises; never exposes the URL path."""
    masked = mask_url(url)
    headers = {"User-Agent": "Reframe/0.02", "Accept": "text/calendar, */*;q=0.5"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read()
            new_etag = resp.headers.get("ETag")
            new_lm = resp.headers.get("Last-Modified")
        if status == 304:
            return None, IcsRead(events=[], not_modified=True, etag=new_etag or etag,
                                 last_modified=new_lm or last_modified)
        if status != 200:
            return None, IcsRead(events=[], complete=False, error=f"HTTP {status} from {masked}")
        return body, IcsRead(events=[], etag=new_etag, last_modified=new_lm)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, IcsRead(events=[], not_modified=True,
                                 etag=exc.headers.get("ETag") or etag,
                                 last_modified=exc.headers.get("Last-Modified") or last_modified)
        return None, IcsRead(events=[], complete=False, error=f"HTTP {exc.code} from {masked}")
    except (socket.timeout, TimeoutError):
        return None, IcsRead(events=[], complete=False, error=f"timed out after {timeout:g} s reading {masked}")
    except urllib.error.URLError as exc:
        reason = _scrub(str(exc.reason), url)
        return None, IcsRead(events=[], complete=False, error=f"could not reach {masked}: {reason}")
    except Exception as exc:  # noqa: BLE001
        return None, IcsRead(events=[], complete=False,
                             error=f"could not read {masked}: {type(exc).__name__}")


def read_source(location: str, win_start: dt.date, win_end: dt.date, *,
                etag: str | None = None, last_modified: str | None = None,
                tz: dt.tzinfo | None = None, timeout: float = 30.0) -> IcsRead:
    """Read and parse one ICS source (file path or URL). Never raises."""
    try:
        kind, loc = normalize_location(location)
    except ValueError as exc:
        return IcsRead(events=[], complete=False, error=f"invalid calendar location: {exc}")
    if kind == "file":
        try:
            with open(loc, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            return IcsRead(events=[], complete=False, error=f"file not found: {loc}")
        except OSError as exc:
            return IcsRead(events=[], complete=False,
                           error=f"could not read {loc}: {exc.strerror or type(exc).__name__}")
        return parse_and_expand(data, win_start, win_end, tz=tz)
    data, meta = fetch(loc, etag=etag, last_modified=last_modified, timeout=timeout)
    if data is None:
        return meta
    result = parse_and_expand(data, win_start, win_end, tz=tz)
    result.etag, result.last_modified = meta.etag, meta.last_modified
    return result
