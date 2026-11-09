"""Read-only Bitfinex daily files for the EXT2 screen.

The ext2 root is an argument. Nothing here embeds a machine path. Trades build
the 1m bars. Candle files are a cross-check and are not copied onto bars.
Every manifest file is hashed before any row is parsed. A RED time gap stops
the load; the day is not dropped and the rest is not returned.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research.ext2_costs import Ext2Stop, dates_inclusive

LAST_READ_DAY = date(2025, 1, 28)
WARMUP_START = date(2024, 10, 30)
FORBIDDEN_MTS = int(datetime(2025, 1, 29, tzinfo=timezone.utc).timestamp() * 1000)
MAX_GAP_MS = 60 * 60 * 1000
INSTRUMENT_REL = Path("external-data") / "bitfinex" / "tBTCF0USTF0"


def default_allowlist_dates() -> tuple[date, ...]:
    return dates_inclusive(WARMUP_START, LAST_READ_DAY)


def allowlist_paths(days: Sequence[date] | None = None) -> tuple[str, ...]:
    chosen = tuple(days) if days is not None else default_allowlist_dates()
    paths = []
    for day in chosen:
        if day > LAST_READ_DAY:
            raise Ext2Stop("STOP_DATE")
        stamp = day.isoformat()
        paths.append(f"trades/{stamp}.json.gz")
        paths.append(f"candles1m/{stamp}.json.gz")
    return tuple(paths)


def _instrument_dir(root: Path) -> Path:
    root = Path(root)
    if (root / "trades").is_dir():
        return root
    nested = root / INSTRUMENT_REL
    if (nested / "trades").is_dir():
        return nested
    raise Ext2Stop("STOP_ROOT")


def _day_of(rel: str) -> date:
    name = Path(rel).name
    stamp = name.split(".", 1)[0]
    try:
        return date.fromisoformat(stamp)
    except ValueError as exc:
        raise Ext2Stop("STOP_DATE") from exc


def _check_name(rel: str, allow: set[str]) -> date:
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or path.parts[0] not in {"trades", "candles1m"}:
        raise Ext2Stop("STOP_PATH")
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


def aggregate_manifest_sha(entries: Mapping[str, str]) -> str:
    lines = [f"{path}  {entries[path]}\n" for path in sorted(entries)]
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def verify_manifest_shas(root: Path, manifest: Mapping[str, str], *, allowlist: Sequence[str] | None = None) -> None:
    """Hash every manifest file before the caller parses any of them."""
    allow = set(allowlist) if allowlist is not None else set(allowlist_paths())
    for rel in manifest:
        _check_name(rel, allow)
    instrument = _instrument_dir(root)
    for rel, expected in manifest.items():
        path = _join(instrument, rel)
        if not path.is_file():
            raise Ext2Stop("STOP_MISSING_FILE")
        if file_sha256(path) != expected:
            raise Ext2Stop("STOP_SHA_MISMATCH")


def _parse_rows(blob: bytes) -> list[Any]:
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as handle:
        payload = json.loads(handle.read().decode())
    if not isinstance(payload, list):
        raise Ext2Stop("STOP_PARSE")
    return payload


def _trade_bars(rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    parsed = []
    last_id = None
    last_mts = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            raise Ext2Stop("STOP_PARSE")
        trade_id, mts, amount, price = int(row[0]), int(row[1]), float(row[2]), float(row[3])
        if mts >= FORBIDDEN_MTS:
            raise Ext2Stop("STOP_MTS")
        if last_mts is not None and mts < last_mts:
            raise Ext2Stop("STOP_DATA")
        if last_mts is not None and mts - last_mts > MAX_GAP_MS:
            raise Ext2Stop("STOP_RED_GAP")
        if last_id is not None and trade_id <= last_id:
            raise Ext2Stop("STOP_DATA")
        last_id, last_mts = trade_id, mts
        parsed.append((mts, amount, price))
    buckets: dict[int, dict[str, Any]] = {}
    for mts, amount, price in parsed:
        minute = mts // 60000 * 60000
        if mts >= minute + 60000:
            raise Ext2Stop("STOP_BAR")
        bar = buckets.get(minute)
        if bar is None:
            bar = {
                "source": "trades",
                "open_mts": minute,
                "close_mts": minute + 60000,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "trade_count": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "volume": 0.0,
                "vwap_notional": 0.0,
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
    bars = []
    for minute in sorted(buckets):
        bar = buckets[minute]
        bar["vwap"] = bar["vwap_notional"] / bar["volume"] if bar["volume"] else bar["close"]
        del bar["vwap_notional"]
        bars.append(bar)
    return bars


def _candle_minutes(rows: Sequence[Sequence[Any]]) -> list[int]:
    minutes = []
    last = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise Ext2Stop("STOP_PARSE")
        mts = int(row[0])
        if mts >= FORBIDDEN_MTS:
            raise Ext2Stop("STOP_MTS")
        if last is not None and mts <= last:
            raise Ext2Stop("STOP_DATA")
        last = mts
        minutes.append(mts // 60000 * 60000)
    return minutes


def load_bitfinex(root: Path | str, manifest: Mapping[str, str], *,
                  allowlist: Sequence[str] | None = None) -> dict[str, Any]:
    allow = set(allowlist) if allowlist is not None else set(allowlist_paths())
    for rel in manifest:
        _check_name(rel, allow)
    instrument = _instrument_dir(Path(root))
    blobs: dict[str, bytes] = {}
    for rel, expected in manifest.items():
        path = _join(instrument, rel)
        if not path.is_file():
            raise Ext2Stop("STOP_MISSING_FILE")
        digest = file_sha256(path)
        if digest != expected:
            raise Ext2Stop("STOP_SHA_MISMATCH")
        blobs[rel] = path.read_bytes()
        if hashlib.sha256(blobs[rel]).hexdigest() != digest:
            raise Ext2Stop("STOP_SHA_MISMATCH")
    bars: list[dict[str, Any]] = []
    trade_minutes: list[int] = []
    candle_minutes: list[int] = []
    for rel in sorted(blobs):
        rows = _parse_rows(blobs[rel])
        if rel.startswith("trades/"):
            day_bars = _trade_bars(rows)
            bars.extend(day_bars)
            trade_minutes.extend(bar["open_mts"] for bar in day_bars)
        else:
            candle_minutes.extend(_candle_minutes(rows))
    holes = sorted(set(trade_minutes) - set(candle_minutes))
    return {
        "schema": "ext2_load_v1",
        "series": "EXT2-EXTERNAL",
        "live": False,
        "paper_only": True,
        "bars": bars,
        "candle_cross_check": {
            "trade_minutes": len(set(trade_minutes)),
            "candle_minutes": len(set(candle_minutes)),
            "candle_holes": holes,
        },
        "files_hashed": len(blobs),
        "red_gap": False,
    }
