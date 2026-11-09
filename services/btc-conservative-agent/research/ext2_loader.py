"""Stream Bitfinex daily *.jsonl.gz files for the EXT2 screen.

The root is an argument. Files are line-delimited JSON arrays. The 182-file
allowlist and the aggregate manifest sha are checked before any row is parsed.
A RED gap anywhere in the continuous series stops the load. Bars are a full
UTC minute grid built from trades (A4): empty minutes after the first trade
carry the previous close for features only and are never prints or fills.
Candle files are a cross-check only.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

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


def _continuity_arrays(ids: np.ndarray, mts: np.ndarray, days: Sequence[date]) -> None:
    """Vectorised RED check on (id, mts) already sorted by (mts, id)."""
    if mts.size and int(mts.max()) >= FORBIDDEN_MTS:
        raise Ext2Stop("STOP_MTS")
    if mts.size > 1:
        if np.any(np.diff(mts) < 0):
            raise Ext2Stop("STOP_DATA")
        if np.any(np.diff(ids) <= 0):
            raise Ext2Stop("STOP_DATA")
        if np.any(np.diff(mts) > MAX_GAP_MS):
            raise Ext2Stop("STOP_RED_GAP")
    starts = np.asarray([day_start_ms(day) for day in days], dtype=np.int64)
    if mts.size == 0:
        raise Ext2Stop("STOP_RED_GAP")
    slot = np.searchsorted(starts, mts, side="right") - 1
    if np.any(slot < 0) or np.any(mts >= starts[-1] + DAY_MS):
        raise Ext2Stop("STOP_RED_GAP")
    if np.any(mts - starts[slot] >= DAY_MS):
        raise Ext2Stop("STOP_RED_GAP")
    first = np.searchsorted(slot, np.arange(len(days)), side="left")
    after = np.searchsorted(slot, np.arange(len(days)), side="right")
    if np.any(after <= first):
        raise Ext2Stop("STOP_RED_GAP")
    if np.any(mts[first] - starts > MAX_GAP_MS):
        raise Ext2Stop("STOP_RED_GAP")
    if len(days) > 1:
        prev_end = mts[after[:-1] - 1]
        if np.any(mts[first[1:]] - prev_end > MAX_GAP_MS):
            raise Ext2Stop("STOP_RED_GAP")


def assert_trade_continuity(rows: Sequence[tuple[int, int, float, float]], days: Sequence[date]) -> None:
    """RED if any gap exceeds 60 minutes, including midnight edges and foreign rows."""
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    ids = np.asarray([row[0] for row in ordered], dtype=np.int64)
    mts = np.asarray([row[1] for row in ordered], dtype=np.int64)
    allowed = set(days)
    for stamp in mts.tolist():
        if stamp >= FORBIDDEN_MTS:
            raise Ext2Stop("STOP_MTS")
        if datetime.fromtimestamp(stamp / 1000.0, timezone.utc).date() not in allowed:
            raise Ext2Stop("STOP_RED_GAP")
    _continuity_arrays(ids, mts, sorted(days))


def trades_to_arrays(rows: Sequence[tuple[int, int, float, float]]) -> dict[str, np.ndarray]:
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    return {
        "id": np.asarray([row[0] for row in ordered], dtype=np.int64),
        "mts": np.asarray([row[1] for row in ordered], dtype=np.int64),
        "amount": np.asarray([row[2] for row in ordered], dtype=np.float64),
        "price": np.asarray([row[3] for row in ordered], dtype=np.float64),
    }


def build_grid(trades: Mapping[str, np.ndarray], days: Sequence[date]) -> dict[str, np.ndarray]:
    """A4 full UTC minute grid from trades sorted by (mts, id).

    Trade-present bar: OHLC from prints, volume, buy, sell, count, vwap.
    Empty bar after the first trade: open=high=low=close = previous close
    (carry-forward for features only), count 0, vwap undefined, empty=True.
    Empty bars are never prints and never fill. Bars before the first trade
    of the series are undefined (NaN close).
    """
    mts = np.asarray(trades["mts"], dtype=np.int64)
    amount = np.asarray(trades["amount"], dtype=np.float64)
    price = np.asarray(trades["price"], dtype=np.float64)
    if mts.size == 0:
        raise Ext2Stop("STOP_RED_GAP")
    if mts.size > 1 and np.any(np.diff(mts) < 0):
        raise Ext2Stop("STOP_DATA")
    origin = day_start_ms(days[0])
    end = day_start_ms(days[-1]) + DAY_MS
    if int(mts[0]) < origin or int(mts[-1]) >= end:
        raise Ext2Stop("STOP_BAR")
    n = int((end - origin) // MINUTE_MS)
    minute = ((mts - origin) // MINUTE_MS).astype(np.int64)
    count = np.bincount(minute, minlength=n).astype(np.int64)
    size = np.abs(amount)
    volume = np.bincount(minute, weights=size, minlength=n)
    notional = np.bincount(minute, weights=size * price, minlength=n)
    buy = np.bincount(minute, weights=np.where(amount > 0.0, amount, 0.0), minlength=n)
    sell = np.bincount(minute, weights=np.where(amount < 0.0, -amount, 0.0), minlength=n)
    start_idx = np.searchsorted(minute, np.arange(n), side="left").astype(np.int64)
    end_idx = np.searchsorted(minute, np.arange(n), side="right").astype(np.int64)
    present = count > 0
    raw_open = np.full(n, np.nan)
    raw_close = np.full(n, np.nan)
    raw_high = np.full(n, np.nan)
    raw_low = np.full(n, np.nan)
    raw_open[present] = price[start_idx[present]]
    raw_close[present] = price[end_idx[present] - 1]
    if np.any(present):
        firsts = start_idx[present]
        raw_high[present] = np.maximum.reduceat(price, firsts)
        raw_low[present] = np.minimum.reduceat(price, firsts)
    last = np.where(present, np.arange(n), -1)
    np.maximum.accumulate(last, out=last)
    defined = last >= 0
    close = np.full(n, np.nan)
    close[defined] = raw_close[last[defined]]
    carried = defined & ~present
    open_ = np.where(present, raw_open, close)
    high = np.where(present, raw_high, close)
    low = np.where(present, raw_low, close)
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = np.where(present & (volume > 0.0), notional / np.where(volume > 0.0, volume, 1.0), np.nan)
    open_mts = origin + np.arange(n, dtype=np.int64) * MINUTE_MS
    return {
        "source": "trades",
        "origin_mts": origin,
        "open_mts": open_mts,
        "close_mts": open_mts + MINUTE_MS,
        "open": open_, "high": high, "low": low, "close": close,
        "trade_count": count, "buy_amount": buy, "sell_amount": sell,
        "volume": volume, "notional": notional, "vwap": vwap,
        "empty": ~present, "carried": carried, "defined": defined,
        "print_start": start_idx, "print_end": end_idx,
    }


def minute_grid(rows: Sequence[tuple[int, int, float, float]], days: Sequence[date]) -> list[dict[str, Any]]:
    """One bar per UTC minute, as dicts. Empty minutes after the first trade
    carry the previous close for features only; they are never prints."""
    if not rows:
        raise Ext2Stop("STOP_RED_GAP")
    arrays = trades_to_arrays(rows)
    grid = build_grid(arrays, days)
    bars = []
    for i in range(grid["open_mts"].size):
        empty = bool(grid["empty"][i])
        defined = bool(grid["defined"][i])

        def val(key: str) -> float | None:
            value = float(grid[key][i])
            return value if math.isfinite(value) else None
        bars.append({
            "source": "trades",
            "open_mts": int(grid["open_mts"][i]),
            "close_mts": int(grid["close_mts"][i]),
            "open": val("open"), "high": val("high"), "low": val("low"), "close": val("close"),
            "vwap": val("vwap"),
            "last_print_price": val("close"),
            "trade_count": int(grid["trade_count"][i]),
            "buy_amount": float(grid["buy_amount"][i]),
            "sell_amount": float(grid["sell_amount"][i]),
            "volume": float(grid["volume"][i]),
            "empty": empty,
            "carried": bool(grid["carried"][i]),
            "defined": defined,
        })
    return bars


def empty_bar_count(bars: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for bar in bars if bar.get("empty"))


def bar_ohlc_usable_as_print(bar: Mapping[str, Any]) -> bool:
    """Empty minutes are not prints and cannot fill, even when carried."""
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
        if mts >= FORBIDDEN_MTS:
            raise Ext2Stop("STOP_MTS")
        if mts < start or mts >= start + DAY_MS:
            raise Ext2Stop("STOP_RED_GAP")
        rows.append((trade_id, mts, amount, price))
    return rows


def _stream_candle_minutes(instrument: Path, day: date) -> list[int]:
    path = _join(instrument, f"candles1m/{day.isoformat()}.jsonl.gz")
    return [_candle_minute(obj) for obj in iter_jsonl_gz(path)]


def load_bitfinex(root: Path | str, manifest: Mapping[str, str]) -> dict[str, Any]:
    """A1-A4: allowlist + aggregate + per-file sha, stream, RED check, grid."""
    assert_manifest_complete(manifest)
    instrument = _instrument_dir(Path(root))
    verify_manifest_shas(Path(root), manifest)
    days = default_allowlist_dates()
    parts: list[tuple[np.ndarray, ...]] = []
    candle_minutes: set[int] = set()
    for day in days:
        rows = _stream_trades(instrument, day)
        parts.append((
            np.asarray([row[0] for row in rows], dtype=np.int64),
            np.asarray([row[1] for row in rows], dtype=np.int64),
            np.asarray([row[2] for row in rows], dtype=np.float64),
            np.asarray([row[3] for row in rows], dtype=np.float64),
        ))
        candle_minutes.update(_stream_candle_minutes(instrument, day))
    ids = np.concatenate([part[0] for part in parts])
    mts = np.concatenate([part[1] for part in parts])
    amount = np.concatenate([part[2] for part in parts])
    price = np.concatenate([part[3] for part in parts])
    order = np.lexsort((ids, mts))
    trades = {"id": ids[order], "mts": mts[order], "amount": amount[order], "price": price[order]}
    _continuity_arrays(trades["id"], trades["mts"], days)
    grid = build_grid(trades, days)
    trade_minutes = set(grid["open_mts"][~grid["empty"]].tolist())
    holes = sorted(trade_minutes - candle_minutes)
    return {
        "schema": "ext2_load_v2",
        "series": "EXT2-EXTERNAL",
        "live": False,
        "paper_only": True,
        "days": days,
        "grid": grid,
        "trades": trades,
        "candle_cross_check": {
            "trade_minutes": len(trade_minutes),
            "candle_minutes": len(candle_minutes),
            "candle_holes": len(holes),
        },
        "files_hashed": len(manifest),
        "red_gap": False,
        "allowlist_sha": ALLOWLIST_AGGREGATE_SHA,
        "h1_seal_sha": H1_SEAL_SHA,
    }
