#!/usr/bin/env python3
"""Calibrate the adaptive regime tile's frozen realized-volatility cells.

Reads public Bitfinex 1m candles for tBTCF0:USTF0 over a fixed trailing window,
computes the same trailing 15-minute realized volatility the tile computes at
signal time, and prints the 40th/90th percentiles. The printed thresholds are
copied into the registry spec by hand; the tile never recalibrates at runtime.

Usage: python scripts/calibrate-adaptive-regime.py --days 30 --end 2026-10-01T00:00:00Z
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"))
from paper_policy_family_adaptive_regime import RV_WINDOW_MIN, realized_vol_bps  # noqa: E402

URL = "https://api-pub.bitfinex.com/v2/candles/trade:1m:tBTCF0:USTF0/hist"


def fetch(start_ms: int, end_ms: int) -> list[list[float]]:
    rows: dict[int, list[float]] = {}
    cursor = start_ms
    while cursor < end_ms:
        query = f"?start={cursor}&end={end_ms}&limit=10000&sort=1"
        request = urllib.request.Request(URL + query, headers={"User-Agent": "doxed-btc-calibration/1", "Accept": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=30) as resp:
                    batch = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if attempt == 4 or exc.code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"candle fetch failed {exc.code} for {query}") from exc
                time.sleep(5 * (attempt + 1))
        if not batch:
            break
        for ts, o, c, h, l, v in batch:
            rows[int(ts)] = [int(ts), float(o), float(h), float(l), float(c), float(v)]
        last = int(batch[-1][0])
        if last <= cursor:
            break
        cursor = last + 60_000
        time.sleep(1.2)
    return [rows[k] for k in sorted(rows)]


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100.0
    lo, hi = math.floor(rank), math.ceil(rank)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00")).astimezone(timezone.utc)
    end_ms = int(end.timestamp() * 1000)
    start_ms = end_ms - args.days * 86_400_000
    candles = fetch(start_ms, end_ms)
    closes = [row[4] for row in candles]
    gaps = sum(1 for a, b in zip(candles, candles[1:]) if b[0] - a[0] != 60_000)
    series = [
        value for value in (
            realized_vol_bps(closes[i - RV_WINDOW_MIN:i + 1])
            for i in range(RV_WINDOW_MIN, len(closes))
        ) if value is not None
    ]
    receipt = {
        "schema": "adaptive_regime_calibration_v1",
        "source": URL,
        "symbol": "tBTCF0:USTF0",
        "window_start_utc": datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat(),
        "window_end_utc": end.isoformat(),
        "window_days": args.days,
        "candles": len(candles),
        "non_contiguous_steps": gaps,
        "first_candle_utc": datetime.fromtimestamp(candles[0][0] / 1000, timezone.utc).isoformat() if candles else None,
        "last_candle_utc": datetime.fromtimestamp(candles[-1][0] / 1000, timezone.utc).isoformat() if candles else None,
        "rv_definition": f"sqrt(sum of squared 1m log returns over trailing {RV_WINDOW_MIN} closed candles), bps",
        "rv_samples": len(series),
        "p40_bps": round(percentile(series, 40), 4) if series else None,
        "p90_bps": round(percentile(series, 90), 4) if series else None,
        "p50_bps": round(percentile(series, 50), 4) if series else None,
        "p99_bps": round(percentile(series, 99), 4) if series else None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    text = json.dumps(receipt, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if series else 1


if __name__ == "__main__":
    raise SystemExit(main())
