"""Stream Bitfinex daily *.jsonl.gz files for the EXT2 screen.

The root is an argument. Files are line-delimited JSON objects. The 182-file
allowlist and the aggregate manifest sha are checked before any row is parsed.
A RED gap anywhere in the continuous series stops the load. Bars are a full
UTC minute grid built from trades. Candle files are a cross-check only.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research.ext2_costs import Ext2Stop, dates_inclusive

LAST_READ_DAY = date(2025, 1, 28)
WARMUP_START = date(2024, 10, 30)
FORBIDDEN_MTS = int(datetime(2025, 1, 29, tzinfo=timezone.utc).timestamp() * 1000)
MAX_GAP_MS = 60 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000
MINUTE_MS = 60 * 1000
ALLOWLIST_AGGREGATE_SHA = "fd4152a6021d664fb35110767f2fcf98804c0f333180be6ee0019e071b2a80c8"
H1_SEAL_SHA = "1f49fc8957510e9ae0141bcaa8f2916c8eac4a2900f836543d369b1a6836e532"
INSTRUMENT_REL = Path("external-data") / "bitfinex" / "tBTCF0USTF0"


def default_allowlist_dates() -> tuple[date, ...]:
    return dates_inclusive(WARMUP_START, LAST_READ_DAY)


def allowlist_paths(days: Sequence[date] | None = None) -> tuple[str, ...]:
    chosen = tuple(days) if days is not None else default_allowlist_dates()
    paths = []
    for day in chosen:
        if day > LAST_READ_DAY or day < WARMUP_START:
            raise Ext2Stop("STOP_DATE")
        stamp = day.isoformat()
        paths.append(f"trades/{stamp}.jsonl.gz")
        paths.append(f"candles1m/{stamp}.jsonl.gz")
    return tuple(paths)


def aggregate_manifest_sha(entries: Mapping[str, str]) -> str:
    lines = [f"{path}  {entries[path]}\n" for path in sorted(entries)]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def _instrument_dir(root: Path) -> Path:
    root = Path(root)
    if (root / "trades").is_dir():
        return root
    nested = root / INSTRUMENT_REL
    if (nested / "trades").is_dir():
        return nested
    raise Ext2Stop("STOP_ROOT")


def _day_of(rel: str) -> date:
    stamp = Path(rel).name.split(".", 1)[0]
    try:
        return date.fromisoformat(stamp)
    except ValueError as exc:
        raise Ext2Stop("STOP_DATE") from exc


def _check_name(rel: str, allow: set[str]) -> date:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or path.parts[0] not in {"trades", "candles1m"}:
        raise Ext2Stop("STOP_PATH")
    if not rel.endswith(".jsonl.gz"):
        raise Ext2Stop("STOP_ALLOWLIST")
    if rel not in allow:
        raise Ext2Stop("STOP_ALLOWLIST")
    day = _day_of(rel)
    if day > LAST_READ_DAY or day < WARMUP_START:
        raise Ext2Stop("STOP_DATE")
    return day


def _join(root: Path, rel: str) -> Path:
    current = root.resolve()
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            raise Ext2Stop("STOP_SYMLINK")
    resolved = current.resolve()
    if root.resolve() not in resolved.parents:
        raise Ext2Stop("STOP_PATH")
    return current


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_jsonl_gz(path: Path) -> Iterator[dict[str, Any]]:
    """Stream one JSON object per line. Any parse error is Ext2Stop."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise Ext2Stop("STOP_PARSE") from exc
                if not isinstance(row, list):
                    raise Ext2Stop("STOP_PARSE")
                yield row
    except Ext2Stop:
        raise
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError) as exc:
        raise Ext2Stop("STOP_PARSE") from exc


def _trade_tuple(row: Sequence[Any]) -> tuple[int, int, float, float]:
    try:
        if len(row) < 4:
            raise Ext2Stop("STOP_PARSE")
        return int(row[0]), int(row[1]), float(row[2]), float(row[3])
    except (TypeError, ValueError) as exc:
        raise Ext2Stop("STOP_PARSE") from exc


def _candle_minute(row: Sequence[Any]) -> int:
    try:
        if len(row) < 6:
            raise Ext2Stop("STOP_PARSE")
        mts = int(row[0])
    except (TypeError, ValueError) as exc:
        raise Ext2Stop("STOP_PARSE") from exc
    if mts >= FORBIDDEN_MTS:
        raise Ext2Stop("STOP_MTS")
    return mts // MINUTE_MS * MINUTE_MS


def day_start_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def assert_trade_continuity(rows: Sequence[tuple[int, int, float, float]], days: Sequence[date]) -> None:
    """RED if any gap exceeds 60 minutes, including midnight edges and foreign rows."""
    allowed = set(days)
    by_day: dict[date, list[int]] = {day: [] for day in days}
    last_id = None
    last_mts = None
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    for trade_id, mts, _amount, _price in ordered:
        if mts >= FORBIDDEN_MTS:
            raise Ext2Stop("STOP_MTS")
        if last_mts is not None and mts < last_mts:
            raise Ext2Stop("STOP_DATA")
        if last_id is not None and trade_id <= last_id:
            raise Ext2Stop("STOP_DATA")
        if last_mts is not None and mts - last_mts > MAX_GAP_MS:
            raise Ext2Stop("STOP_RED_GAP")
        day = datetime.fromtimestamp(mts / 1000.0, timezone.utc).date()
        if day not in allowed:
            raise Ext2Stop("STOP_RED_GAP")
        start = day_start_ms(day)
        if mts < start or mts >= start + DAY_MS:
            raise Ext2Stop("STOP_RED_GAP")
        by_day[day].append(mts)
        last_id, last_mts = trade_id, mts
    for index, day in enumerate(days):
        start = day_start_ms(day)
        end = start + DAY_MS
        stamps = by_day[day]
        if not stamps:
            raise Ext2Stop("STOP_RED_GAP")
        if stamps[0] - start > MAX_GAP_MS:
            raise Ext2Stop("STOP_RED_GAP")
        if index:
            prev_end = by_day[days[index - 1]][-1]
            if stamps[0] - prev_end > MAX_GAP_MS:
                raise Ext2Stop("STOP_RED_GAP")


def minute_grid(rows: Sequence[tuple[int, int, float, float]], days: Sequence[date]) -> list[dict[str, Any]]:
    """One bar per UTC minute. Empty minutes keep the previous close and zero volume."""
    buckets: dict[int, dict[str, Any]] = {}
    for _trade_id, mts, amount, price in sorted(rows, key=lambda item: item[1]):
        minute = mts // MINUTE_MS * MINUTE_MS
        if mts >= minute + MINUTE_MS:
            raise Ext2Stop("STOP_BAR")
        bar = buckets.get(minute)
        if bar is None:
            bar = {
                "source": "trades",
                "open_mts": minute,
                "close_mts": minute + MINUTE_MS,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "trade_count": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "volume": 0.0,
                "vwap_notional": 0.0,
                "empty": False,
            }
            buckets[minute] = bar
        bar["high"] = max(bar["high"], price)
        bar["low"] = min(bar["low"], price)
        bar["close"] = price
        bar["trade_count"] += 1
        size = abs(amount)
        bar["volume"] += size
        bar["vwap_notional"] += size * price
        if amount > 0.0:
            bar["buy_amount"] += amount
        elif amount < 0.0:
            bar["sell_amount"] += -amount
    if not buckets:
        raise Ext2Stop("STOP_RED_GAP")
    first_minute = min(buckets)
    last_close = None
    bars = []
    cursor = day_start_ms(days[0])
    end = day_start_ms(days[-1]) + DAY_MS
    while cursor < end:
        found = buckets.get(cursor)
        if found is None:
            found = {
                "source": "trades", "open_mts": cursor, "close_mts": cursor + MINUTE_MS,
                "open": None, "high": None, "low": None, "close": None, "vwap": None,
                "last_print_price": last_close,
                "trade_count": 0, "buy_amount": 0.0, "sell_amount": 0.0, "volume": 0.0,
                "empty": True, "defined": False,
            }
        else:
            found["vwap"] = found["vwap_notional"] / found["volume"] if found["volume"] else found["close"]
            found["defined"] = True
            found["last_print_price"] = float(found["close"])
            last_close = float(found["close"])
        found.pop("vwap_notional", None)
        bars.append(found)
        cursor += MINUTE_MS
    return bars


def empty_bar_count(bars: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for bar in bars if bar.get("empty"))


def bar_ohlc_usable_as_print(bar: Mapping[str, Any]) -> bool:
    """Empty minutes are not prints and cannot fill."""
    return not bar.get("empty") and bar.get("trade_count", 0) > 0 and bar.get("close") is not None


def assert_manifest_complete(manifest: Mapping[str, str]) -> None:
    allow = allowlist_paths()
    if set(manifest) != set(allow):
        raise Ext2Stop("STOP_ALLOWLIST")
    if aggregate_manifest_sha(manifest) != ALLOWLIST_AGGREGATE_SHA:
        raise Ext2Stop("STOP_AGGREGATE_SHA")


def verify_manifest_shas(root: Path, manifest: Mapping[str, str]) -> None:
    assert_manifest_complete(manifest)
    instrument = _instrument_dir(root)
    allow = set(allowlist_paths())
    for rel, expected in manifest.items():
        _check_name(rel, allow)
        path = _join(instrument, rel)
        if not path.is_file():
            raise Ext2Stop("STOP_MISSING_FILE")
        if file_sha256(path) != expected:
            raise Ext2Stop("STOP_SHA_MISMATCH")


def _stream_trades(instrument: Path, day: date) -> list[tuple[int, int, float, float]]:
    path = _join(instrument, f"trades/{day.isoformat()}.jsonl.gz")
    rows = []
    start = day_start_ms(day)
    for obj in iter_jsonl_gz(path):
        trade_id, mts, amount, price = _trade_tuple(obj)
        if mts < start or mts >= start + DAY_MS:
            raise Ext2Stop("STOP_RED_GAP")
        rows.append((trade_id, mts, amount, price))
    return rows


def _stream_candle_minutes(instrument: Path, day: date) -> list[int]:
    path = _join(instrument, f"candles1m/{day.isoformat()}.jsonl.gz")
    return [_candle_minute(obj) for obj in iter_jsonl_gz(path)]


def load_bitfinex(root: Path | str, manifest: Mapping[str, str]) -> dict[str, Any]:
    assert_manifest_complete(manifest)
    instrument = _instrument_dir(Path(root))
    verify_manifest_shas(Path(root), manifest)
    days = default_allowlist_dates()
    trades: list[tuple[int, int, float, float]] = []
    candle_minutes: list[int] = []
    for day in days:
        trades.extend(_stream_trades(instrument, day))
        candle_minutes.extend(_stream_candle_minutes(instrument, day))
    assert_trade_continuity(trades, days)
    bars = minute_grid(trades, days)
    trade_minutes = [bar["open_mts"] for bar in bars if not bar["empty"]]
    holes = sorted(set(trade_minutes) - set(candle_minutes))
    return {
        "schema": "ext2_load_v1",
        "series": "EXT2-EXTERNAL",
        "live": False,
        "paper_only": True,
        "bars": bars,
        "trades": trades,
        "candle_cross_check": {
            "trade_minutes": len(set(trade_minutes)),
            "candle_minutes": len(set(candle_minutes)),
            "candle_holes": holes,
        },
        "files_hashed": len(manifest),
        "red_gap": False,
        "allowlist_sha": ALLOWLIST_AGGREGATE_SHA,
        "h1_seal_sha": H1_SEAL_SHA,
    }
