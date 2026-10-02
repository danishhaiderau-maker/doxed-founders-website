"""Session and macro-calendar flags derived only from a UTC timestamp.

Pure functions shared by the market-context collector (which stamps every
minute row) and the analyzer (which recomputes them for any timestamp). No
network, no tzdata dependency: US and EU daylight-saving transitions are
computed from their statutory rules.

The macro calendar is a static, checked-in yearly table of tier-1 US releases
(FOMC statement, CPI, Employment Situation/NFP) in US Eastern local time. A
timestamp in a year without a table yields ``calendar_status =
CALENDAR_MISSING_YEAR`` instead of silently reporting "no event". Add next
year's table before 1 January; ``test_market_session_calendar`` fails once the
current UTC year has no table.

Sources (verified 2026-10-02): bls.gov/schedule/news_release/cpi.htm,
bls.gov/schedule/news_release/empsit.htm, federalreserve.gov FOMC calendar.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional

SCHEMA = "market_session_flags_v1"
CALENDAR_VERSION = "macro_calendar_2026_v1_20261002"

# (kind, local ET date, local ET time). FOMC is the 14:00 ET statement on the
# second meeting day; CPI and NFP are 08:30 ET.
MACRO_EVENTS_ET = {
    2026: (
        ("NFP", "2026-01-09", "08:30"), ("CPI", "2026-01-13", "08:30"),
        ("FOMC", "2026-01-28", "14:00"),
        ("NFP", "2026-02-11", "08:30"), ("CPI", "2026-02-13", "08:30"),
        ("NFP", "2026-03-06", "08:30"), ("CPI", "2026-03-11", "08:30"),
        ("FOMC", "2026-03-18", "14:00"),
        ("NFP", "2026-04-03", "08:30"), ("CPI", "2026-04-10", "08:30"),
        ("FOMC", "2026-04-29", "14:00"),
        ("NFP", "2026-05-08", "08:30"), ("CPI", "2026-05-12", "08:30"),
        ("NFP", "2026-06-05", "08:30"), ("CPI", "2026-06-10", "08:30"),
        ("FOMC", "2026-06-17", "14:00"),
        ("NFP", "2026-07-02", "08:30"), ("CPI", "2026-07-14", "08:30"),
        ("FOMC", "2026-07-29", "14:00"),
        ("NFP", "2026-08-07", "08:30"), ("CPI", "2026-08-12", "08:30"),
        ("NFP", "2026-09-04", "08:30"), ("CPI", "2026-09-11", "08:30"),
        ("FOMC", "2026-09-16", "14:00"),
        ("NFP", "2026-10-02", "08:30"), ("CPI", "2026-10-14", "08:30"),
        ("FOMC", "2026-10-28", "14:00"),
        ("NFP", "2026-11-06", "08:30"), ("CPI", "2026-11-10", "08:30"),
        ("NFP", "2026-12-04", "08:30"), ("FOMC", "2026-12-09", "14:00"),
        ("CPI", "2026-12-10", "08:30"),
    ),
}

# Minutes around a release treated as its event window (pre, post).
MACRO_WINDOW_MIN = (30, 60)
FUNDING_HOURS_UTC = (0, 8, 16)
FUNDING_WINDOW_MIN = 10
OPEN_WINDOW_MIN = 30


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> int:
    """Day of month of the n-th ``weekday`` (Mon=0); n=-1 means the last."""
    days = [d for d in range(1, calendar.monthrange(year, month)[1] + 1)
            if calendar.weekday(year, month, d) == weekday]
    return days[n] if n < 0 else days[n - 1]


def us_dst(ts: float) -> bool:
    """US DST: 2nd Sunday of March 02:00 local to 1st Sunday of November 02:00."""
    y = datetime.fromtimestamp(ts, timezone.utc).year
    start = datetime(y, 3, _nth_weekday(y, 3, 6, 2), 7, tzinfo=timezone.utc)
    end = datetime(y, 11, _nth_weekday(y, 11, 6, 1), 6, tzinfo=timezone.utc)
    return start.timestamp() <= ts < end.timestamp()


def eu_dst(ts: float) -> bool:
    """EU summer time: last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC."""
    y = datetime.fromtimestamp(ts, timezone.utc).year
    start = datetime(y, 3, _nth_weekday(y, 3, 6, -1), 1, tzinfo=timezone.utc)
    end = datetime(y, 10, _nth_weekday(y, 10, 6, -1), 1, tzinfo=timezone.utc)
    return start.timestamp() <= ts < end.timestamp()


def et_to_utc_ts(date_str: str, time_str: str) -> float:
    local = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    guess = local.replace(tzinfo=timezone.utc) + timedelta(hours=5)
    offset = 4 if us_dst(guess.timestamp()) else 5
    return (local.replace(tzinfo=timezone.utc) + timedelta(hours=offset)).timestamp()


def macro_events_utc(year: int) -> Optional[list]:
    table = MACRO_EVENTS_ET.get(int(year))
    if table is None:
        return None
    return sorted((et_to_utc_ts(d, t), kind) for kind, d, t in table)


def _day_open(ts: float, hour: int, minute: int) -> float:
    day = datetime.fromtimestamp(ts, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return day.timestamp() + hour * 3600 + minute * 60


def _minutes(delta_sec: float) -> float:
    return round(delta_sec / 60.0, 2)


def session_flags(ts: float) -> dict:
    """Session, weekend, open-window and funding-window flags for ``ts`` (UTC epoch)."""
    ts = float(ts)
    dt = datetime.fromtimestamp(ts, timezone.utc)
    weekday = dt.weekday()
    weekend = weekday >= 5
    us_open = _day_open(ts, 13 if us_dst(ts) else 14, 30)
    us_close = us_open + 6.5 * 3600
    eu_open = _day_open(ts, 7 if eu_dst(ts) else 8, 0)
    eu_close = eu_open + 8.5 * 3600
    asia_open = _day_open(ts, 0, 0)
    asia_close = asia_open + 8 * 3600
    us_cash = (not weekend) and us_open <= ts < us_close
    eu_cash = (not weekend) and eu_open <= ts < eu_close
    asia_cash = (not weekend) and asia_open <= ts < asia_close
    # CME crypto futures: closed Fri 17:00 ET -> Sun 18:00 ET and 17:00-18:00 ET daily.
    et_hour = (dt - timedelta(hours=4 if us_dst(ts) else 5))
    et_minutes = et_hour.hour * 60 + et_hour.minute
    cme_closed = (
        (et_hour.weekday() == 4 and et_minutes >= 17 * 60)
        or et_hour.weekday() == 5
        or (et_hour.weekday() == 6 and et_minutes < 18 * 60)
        or (17 * 60 <= et_minutes < 18 * 60)
    )
    if us_cash and eu_cash:
        primary = "EU_US_OVERLAP"
    elif us_cash:
        primary = "US"
    elif eu_cash:
        primary = "EU"
    elif asia_cash:
        primary = "ASIA"
    else:
        primary = "OFF_HOURS"
    funding_marks = [_day_open(ts, h, 0) for h in FUNDING_HOURS_UTC] + [_day_open(ts, 0, 0) + 86400]
    next_funding = min(m for m in funding_marks if m > ts) if any(m > ts for m in funding_marks) else None
    prev_funding = max((m for m in funding_marks + [_day_open(ts, 16, 0) - 86400] if m <= ts), default=None)
    min_to_funding = None if next_funding is None else _minutes(next_funding - ts)
    min_since_funding = None if prev_funding is None else _minutes(ts - prev_funding)
    return {
        "session_primary": "WEEKEND" if weekend else primary,
        "weekday_utc": weekday,
        "hour_utc": dt.hour,
        "is_weekend": weekend,
        "us_dst": us_dst(ts),
        "eu_dst": eu_dst(ts),
        "asia_cash_open": asia_cash,
        "eu_cash_open": eu_cash,
        "us_cash_open": us_cash,
        "cme_open": not cme_closed,
        "min_from_asia_open": _minutes(ts - asia_open),
        "min_from_eu_open": _minutes(ts - eu_open),
        "min_from_us_open": _minutes(ts - us_open),
        "asia_open_window": (not weekend) and 0 <= ts - asia_open < OPEN_WINDOW_MIN * 60,
        "eu_open_window": (not weekend) and 0 <= ts - eu_open < OPEN_WINDOW_MIN * 60,
        "us_open_window": (not weekend) and 0 <= ts - us_open < OPEN_WINDOW_MIN * 60,
        "min_to_funding_utc_8h": min_to_funding,
        "min_since_funding_utc_8h": min_since_funding,
        "funding_window": bool(
            (min_to_funding is not None and min_to_funding <= FUNDING_WINDOW_MIN)
            or (min_since_funding is not None and min_since_funding < FUNDING_WINDOW_MIN)
        ),
    }


def macro_flags(ts: float) -> dict:
    ts = float(ts)
    year = datetime.fromtimestamp(ts, timezone.utc).year
    events = macro_events_utc(year)
    out = {
        "calendar_version": CALENDAR_VERSION,
        "calendar_status": "OK",
        "macro_window": False,
        "macro_window_kind": None,
        "next_macro_kind": None,
        "min_to_next_macro": None,
        "last_macro_kind": None,
        "min_since_last_macro": None,
        "macro_today": [],
    }
    if events is None:
        out["calendar_status"] = "CALENDAR_MISSING_YEAR"
        return out
    nxt = macro_events_utc(year + 1) or []
    prv = macro_events_utc(year - 1) or []
    allev = prv + events + nxt
    future = [(t, k) for t, k in allev if t > ts]
    past = [(t, k) for t, k in allev if t <= ts]
    if future:
        out["next_macro_kind"], out["min_to_next_macro"] = future[0][1], _minutes(future[0][0] - ts)
    if past:
        out["last_macro_kind"], out["min_since_last_macro"] = past[-1][1], _minutes(ts - past[-1][0])
    day = datetime.fromtimestamp(ts, timezone.utc).date()
    out["macro_today"] = [k for t, k in events if datetime.fromtimestamp(t, timezone.utc).date() == day]
    pre, post = MACRO_WINDOW_MIN
    for t, k in allev:
        if t - pre * 60 <= ts < t + post * 60:
            out["macro_window"], out["macro_window_kind"] = True, k
            break
    return out


def flags(ts: float) -> dict:
    return {"schema": SCHEMA, **session_flags(ts), **macro_flags(ts)}
