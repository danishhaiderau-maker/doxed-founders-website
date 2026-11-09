"""Synthetic Bitfinex files. No machine path and no row after 2025-01-28."""
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research import ext2_loader as loader
from research.ext2_search import features_from_trade_bars


def _ms(day: str, minute: int = 0) -> int:
    base = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)
    return base + minute * 60_000


def _write(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as handle:
        handle.write(json.dumps(rows).encode())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pack(root: Path, day: str = "2024-11-01"):
    trades = [[index + 1, _ms(day, index), 0.01, 100.0 + index] for index in range(5)]
    candles = [[_ms(day, index), 100.0, 50.0, 101.0, 99.0, 1.0] for index in range(5)]
    trade_sha = _write(root / "trades" / f"{day}.json.gz", trades)
    candle_sha = _write(root / "candles1m" / f"{day}.json.gz", candles)
    manifest = {f"trades/{day}.json.gz": trade_sha, f"candles1m/{day}.json.gz": candle_sha}
    allow = list(manifest)
    return manifest, allow


def test_bars_come_from_trades_and_features_do_not_read_candles(tmp_path):
    manifest, allow = _pack(tmp_path)
    loaded = loader.load_bitfinex(tmp_path, manifest, allowlist=allow)
    assert loaded["bars"][0]["source"] == "trades"
    assert loaded["bars"][-1]["close"] == 104.0
    assert "candle" not in json.dumps(loaded["bars"]).lower()
    features = features_from_trade_bars(loaded["bars"])
    assert features[-1]["close"] == 104.0
    candles = [[_ms("2024-11-01", index), 1.0, 9_999.0, 1.0, 1.0, 1.0] for index in range(5)]
    candle_sha = _write(tmp_path / "candles1m" / "2024-11-01.json.gz", candles)
    manifest["candles1m/2024-11-01.json.gz"] = candle_sha
    again = loader.load_bitfinex(tmp_path, manifest, allowlist=allow)
    assert features_from_trade_bars(again["bars"]) == features
    assert again["bars"][-1]["close"] == 104.0


def test_feature_function_does_not_mention_candles():
    import inspect
    source = inspect.getsource(features_from_trade_bars).lower()
    assert "candle" not in source


def test_sha_mismatch_stops_before_a_later_parse(tmp_path):
    day = "2024-11-02"
    good = _write(tmp_path / "trades" / f"{day}.json.gz", [[1, _ms(day), 0.1, 100.0]])
    garbage = tmp_path / "candles1m" / f"{day}.json.gz"
    garbage.parent.mkdir(parents=True, exist_ok=True)
    garbage.write_bytes(b"not a gzip")
    manifest = {
        f"trades/{day}.json.gz": good,
        f"candles1m/{day}.json.gz": "0" * 64,
    }
    allow = list(manifest)
    with pytest.raises(loader.Ext2Stop, match="STOP_SHA_MISMATCH"):
        loader.load_bitfinex(tmp_path, manifest, allowlist=allow)


def test_date_after_the_ceiling_is_not_opened(tmp_path):
    rel = "trades/2025-01-29.json.gz"
    manifest = {rel: "a" * 64}
    with pytest.raises(loader.Ext2Stop, match="STOP_DATE"):
        loader.load_bitfinex(tmp_path, manifest, allowlist=[rel])
    assert not (tmp_path / rel).exists()


def test_red_gap_in_warmup_or_w1_stops_without_dropping_the_day(tmp_path):
    warmup = "2024-10-30"
    late = _ms(warmup) + 61 * 60_000
    trade_sha = _write(
        tmp_path / "trades" / f"{warmup}.json.gz",
        [[1, _ms(warmup), 0.1, 100.0], [2, late, 0.1, 101.0]],
    )
    candle_sha = _write(tmp_path / "candles1m" / f"{warmup}.json.gz", [[_ms(warmup), 1, 1, 1, 1, 1]])
    manifest = {f"trades/{warmup}.json.gz": trade_sha, f"candles1m/{warmup}.json.gz": candle_sha}
    with pytest.raises(loader.Ext2Stop, match="STOP_RED_GAP"):
        loader.load_bitfinex(tmp_path, manifest, allowlist=list(manifest))


def test_two_days_stop_on_the_gapped_day_instead_of_returning_the_clean_one(tmp_path):
    clean, allow = _pack(tmp_path, "2024-11-01")
    bad_day = "2024-11-02"
    late = _ms(bad_day) + 61 * 60_000
    clean[f"trades/{bad_day}.json.gz"] = _write(
        tmp_path / "trades" / f"{bad_day}.json.gz",
        [[1, _ms(bad_day), 0.2, 100.0], [2, late, 0.2, 100.0]],
    )
    clean[f"candles1m/{bad_day}.json.gz"] = _write(
        tmp_path / "candles1m" / f"{bad_day}.json.gz",
        [[_ms(bad_day), 1, 1, 1, 1, 1]],
    )
    allow = list(clean)
    with pytest.raises(loader.Ext2Stop, match="STOP_RED_GAP"):
        loader.load_bitfinex(tmp_path, clean, allowlist=allow)


def test_trade_at_the_forbidden_instant_stops(tmp_path):
    day = "2025-01-28"
    trade_sha = _write(
        tmp_path / "trades" / f"{day}.json.gz",
        [[1, loader.FORBIDDEN_MTS, 0.1, 100.0]],
    )
    candle_sha = _write(tmp_path / "candles1m" / f"{day}.json.gz", [[_ms(day), 1, 1, 1, 1, 1]])
    manifest = {f"trades/{day}.json.gz": trade_sha, f"candles1m/{day}.json.gz": candle_sha}
    with pytest.raises(loader.Ext2Stop, match="STOP_MTS"):
        loader.load_bitfinex(tmp_path, manifest, allowlist=list(manifest))


def test_allowlist_is_warmup_plus_w1_and_the_source_has_no_machine_path():
    assert len(loader.default_allowlist_dates()) == 91
    assert len(loader.allowlist_paths()) == 182
    assert loader.LAST_READ_DAY.isoformat() == "2025-01-28"
    text = Path(loader.__file__).read_text(encoding="utf-8")
    assert "DoxxedCrypto" not in text
    assert "C:\\" not in text


def test_exact_sixty_minute_gap_is_not_a_red_gap():
    rows = [[1, 0, 0.1, 100.0], [2, loader.MAX_GAP_MS, 0.1, 101.0]]
    bars = loader._trade_bars(rows)
    assert len(bars) == 2
    with pytest.raises(loader.Ext2Stop, match="STOP_RED_GAP"):
        loader._trade_bars([[1, 0, 0.1, 100.0], [2, loader.MAX_GAP_MS + 1, 0.1, 101.0]])
