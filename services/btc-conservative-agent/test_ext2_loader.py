"""Synthetic jsonl.gz fixtures. No machine path and no row after 2025-01-28."""
import gzip
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from research import ext2_loader as loader
from research.ext2_search import features_from_trade_bars


def _ms(day: str, minute: int = 0) -> int:
    base = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)
    return base + minute * 60_000


def _jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_streams_jsonl_arrays_and_empty_minutes_are_not_prints(tmp_path):
    day = date(2024, 11, 1)
    start = loader.day_start_ms(day)
    rows = [
        (1, start + 60_000, 0.2, 100.0),
        (2, start + 180_000, -0.1, 101.0),
    ]
    path = tmp_path / "trades" / "2024-11-01.jsonl.gz"
    _jsonl(path, [list(row) for row in rows])
    parsed = [loader._trade_tuple(row) for row in loader.iter_jsonl_gz(path)]
    assert parsed == rows
    bars = loader.minute_grid(rows, [day])
    assert loader.empty_bar_count(bars) > 0
    empty = next(bar for bar in bars if bar["empty"])
    assert empty["trade_count"] == 0
    assert empty["open"] is None and empty["close"] is None
    assert loader.bar_ohlc_usable_as_print(empty) is False
    filled = next(bar for bar in bars if not bar["empty"])
    assert filled["close"] == 100.0
    assert "candle" not in json.dumps(bars).lower()
    assert features_from_trade_bars([filled])[0]["close"] == 100.0


def test_bad_jsonl_line_is_ext2stop(tmp_path):
    path = tmp_path / "bad.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("{not-json}\n")
    with pytest.raises(loader.Ext2Stop, match="STOP_PARSE"):
        list(loader.iter_jsonl_gz(path))


def test_partial_manifest_and_post_ceiling_name_stop(tmp_path):
    with pytest.raises(loader.Ext2Stop, match="STOP_ALLOWLIST"):
        loader.load_bitfinex(tmp_path, {"trades/2024-11-01.jsonl.gz": "ab" * 32})
    with pytest.raises(loader.Ext2Stop, match="STOP_DATE"):
        loader._check_name("trades/2025-01-29.jsonl.gz", {"trades/2025-01-29.jsonl.gz"})


def test_red_gap_covers_midnight_lead_and_a_foreign_row():
    day = date(2024, 11, 1)
    nxt = date(2024, 11, 2)
    start = loader.day_start_ms(day)
    late = start + 5 * 60 * 60 * 1000
    with pytest.raises(loader.Ext2Stop, match="STOP_RED_GAP"):
        loader.assert_trade_continuity([(1, late, 0.1, 100.0)], [day])
    first = start + 1_000
    second = loader.day_start_ms(nxt) + 2 * 60 * 60 * 1000
    with pytest.raises(loader.Ext2Stop, match="STOP_RED_GAP"):
        loader.assert_trade_continuity(
            [(1, first, 0.1, 100.0), (2, second, 0.1, 100.0)], [day, nxt],
        )
    foreign = loader.day_start_ms(nxt) + 1_000
    with pytest.raises(loader.Ext2Stop, match="STOP_RED_GAP"):
        loader.assert_trade_continuity([(1, foreign, 0.1, 100.0)], [day])


def test_exact_sixty_minute_gap_is_allowed_and_sixty_plus_one_is_not():
    day = date(2024, 11, 1)
    start = loader.day_start_ms(day) + 1_000
    loader.assert_trade_continuity(
        [(1, start, 0.1, 100.0), (2, start + loader.MAX_GAP_MS, 0.1, 101.0)],
        [day],
    )
    with pytest.raises(loader.Ext2Stop, match="STOP_RED_GAP"):
        loader.assert_trade_continuity(
            [(1, start, 0.1, 100.0), (2, start + loader.MAX_GAP_MS + 1, 0.1, 101.0)],
            [day],
        )


def test_aggregate_mismatch_stops_before_parse(monkeypatch):
    name = "trades/2024-11-01.jsonl.gz"
    monkeypatch.setattr(loader, "allowlist_paths", lambda days=None: (name,))
    monkeypatch.setattr(loader, "ALLOWLIST_AGGREGATE_SHA", "f" * 64)
    with pytest.raises(loader.Ext2Stop, match="STOP_AGGREGATE_SHA"):
        loader.assert_manifest_complete({name: "ab" * 32})


def test_allowlist_names_are_jsonl_and_the_source_has_no_machine_path():
    assert len(loader.default_allowlist_dates()) == 91
    paths = loader.allowlist_paths()
    assert len(paths) == 182
    assert all(path.endswith(".jsonl.gz") for path in paths)
    assert loader.ALLOWLIST_AGGREGATE_SHA.startswith("fd4152a6021d664f")
    text = Path(loader.__file__).read_text(encoding="utf-8")
    assert "DoxxedCrypto" not in text
    assert "C:\\" not in text


def test_feature_function_does_not_mention_candles():
    import inspect
    assert "candle" not in inspect.getsource(features_from_trade_bars).lower()
