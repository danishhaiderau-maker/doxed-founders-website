import copy

import pytest

from collector_v22 import build_pre_signal_context
from chase_offset_touch_grid import candle_ts_sec


def candle(open_sec, *, milliseconds=False):
    return [open_sec * (1000 if milliseconds else 1), 100, 102, 99, 101, 1]


@pytest.mark.parametrize("milliseconds", [False, True])
def test_all_timeframes_exclude_future_and_forming_bars(milliseconds):
    anchor = 1788480000  # exact UTC-hour boundary
    signal = anchor + 3770
    rows = [candle(anchor + i * 60, milliseconds=milliseconds) for i in range(-60, 75)]
    saved = copy.deepcopy(rows)
    result = build_pre_signal_context(rows, signal_ts=signal)
    for timeframe, seconds in (("1m", 60), ("5m", 300), ("15m", 900), ("1h", 3600)):
        series = result["series"][timeframe]
        assert series["candles"]
        assert all(candle_ts_sec(row) + seconds <= signal for row in series["candles"])
        assert series["coverage_complete"] is False
    assert rows == saved  # no rewriting source history
    assert result["availability_time_verified"] is False


def test_exact_close_is_included_but_still_forming_minute_is_not():
    signal = 1788480120
    result = build_pre_signal_context([candle(signal - 60), candle(signal - 59), candle(signal)], signal_ts=signal)
    assert result["series"]["1m"]["candles"] == [candle(signal - 60)]


def test_real_incident_future_cache_cannot_become_presignal_history():
    signal = 1788423104.3045778
    rows = [candle(1788519060 + 60 * i) for i in range(200)]
    result = build_pre_signal_context(rows, signal_ts=signal)
    for series in result["series"].values():
        assert series["bars"] == 0 and series["candles"] == []
        assert series["temporal_status"] == "UNKNOWN_NO_CLOSED_CAUSAL_CANDLES"
        assert series["coverage_complete"] is False


def test_partial_coarse_bucket_is_not_declared_closed():
    anchor = 1788480000
    result = build_pre_signal_context([candle(anchor), candle(anchor + 60)], signal_ts=anchor + 150)
    assert result["series"]["1m"]["bars"] == 2
    for tf in ("5m", "15m", "1h"):
        assert result["series"][tf]["candles"] == []
