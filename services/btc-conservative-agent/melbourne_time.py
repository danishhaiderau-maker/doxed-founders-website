"""One display-time helper for every human-facing time: Australia/Melbourne.

Danish reads every time in Melbourne local time. All displays (Fly dashboard,
alerts, uptime, system-health watcher, self-aware digest, research dashboard)
format through this module so the zone and its label are the same everywhere
and stay correct through daylight saving:

* zone  ``Australia/Melbourne`` (IANA): UTC+11 in summer, UTC+10 otherwise;
* label ``Melbourne time`` (never a hard-coded "AEST"/"UTC+10").

Stored timestamps stay UTC; this module only formats them for display. The
Windows laptop may lack the ``tzdata`` package, so ``zoneinfo`` can fail
there; the fallback applies Victoria's daylight-saving rule (from 02:00
standard time on the first Sunday of October to 03:00 daylight time on the
first Sunday of April).

``abbrev_stamp`` keeps the offset-carrying ``YYYY-MM-DD HH:MM:SS AEDT`` form
for bot -> API interchange fields, which the website parses back to an
instant; the abbreviation is derived from the zone, not hard-coded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ZONE_NAME = "Australia/Melbourne"
LABEL = "Melbourne time"

try:
    from zoneinfo import ZoneInfo as _ZoneInfo

    _ZONE = _ZoneInfo(ZONE_NAME)
except Exception:  # Windows without tzdata
    _ZONE = None

_STD = timezone(timedelta(hours=10), "AEST")
_DST = timezone(timedelta(hours=11), "AEDT")


def _first_sunday_utc(year: int, month: int) -> datetime:
    day = datetime(year, month, 1, tzinfo=timezone.utc)
    return day + timedelta(days=(6 - day.weekday()) % 7)


def _fallback_zone(moment: datetime) -> timezone:
    utc = moment.astimezone(timezone.utc)
    start = _first_sunday_utc(utc.year, 10) - timedelta(hours=8)  # 02:00 +10 = 16:00Z Saturday
    end = _first_sunday_utc(utc.year, 4) - timedelta(hours=8)     # 03:00 +11 = 16:00Z Saturday
    return _DST if (utc >= start or utc < end) else _STD


def _as_utc(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        return datetime.fromtimestamp(ts / 1000.0 if ts > 1e12 else ts, timezone.utc)
    text = str(value).strip()
    try:
        return _as_utc(float(text))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_melbourne(value) -> datetime | None:
    """UTC epoch seconds/ms, ISO string or datetime -> aware Melbourne datetime."""
    moment = _as_utc(value)
    if moment is None:
        return None
    return moment.astimezone(_ZONE if _ZONE is not None else _fallback_zone(moment))


def utc_offset_hours(value) -> float | None:
    local = to_melbourne(value)
    return None if local is None else local.utcoffset().total_seconds() / 3600.0


def format_melbourne(value, *, date: bool = True, seconds: bool = False, label: bool = True,
                     default: str | None = None) -> str | None:
    """``2026-10-04 19:41 Melbourne time`` (24h). ``date=False`` -> ``19:41 Melbourne time``."""
    local = to_melbourne(value)
    if local is None:
        return default
    fmt = ("%Y-%m-%d " if date else "") + ("%H:%M:%S" if seconds else "%H:%M")
    text = local.strftime(fmt)
    return f"{text} {LABEL}" if label else text


def clock_label(value, now=None) -> str | None:
    """``19:41 Melbourne time`` today, ``03 Oct 19:41 Melbourne time`` on another day."""
    local = to_melbourne(value)
    if local is None:
        return None
    today = to_melbourne(datetime.now(timezone.utc) if now is None else now)
    fmt = "%H:%M" if local.date() == today.date() else "%d %b %H:%M"
    return f"{local.strftime(fmt)} {LABEL}"


def abbrev_stamp(value, default: str | None = None) -> str | None:
    """Interchange form ``2026-10-04 19:41:00 AEDT`` (abbreviation derived from the zone)."""
    local = to_melbourne(value)
    if local is None:
        return default
    return local.strftime("%Y-%m-%d %H:%M:%S ") + local.strftime("%Z")


DESCRIPTION = f"{LABEL} ({ZONE_NAME}; UTC+11 during daylight saving, UTC+10 otherwise)"
