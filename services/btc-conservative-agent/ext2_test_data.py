"""Synthetic EXT2 data for tests and the box dry-check. Never real market data."""
from __future__ import annotations

from datetime import date

import numpy as np

from research.ext2_calendar import Calendar, Fold, day_ms


def _d(text: str) -> date:
    return date.fromisoformat(text)


def small_calendar() -> Calendar:
    """15-day calendar with scaled windows (tests only; a real run refuses it)."""
    return Calendar(
        warmup=(_d("2024-10-30"), _d("2024-10-31")),
        w1=(_d("2024-11-01"), _d("2024-11-13")),
        embargo=(_d("2024-11-14"), _d("2024-11-14")),
        h1=(_d("2024-11-15"), _d("2024-11-16")),
        folds=(
            Fold("F1", (_d("2024-11-01"), _d("2024-11-02")), (_d("2024-11-03"), _d("2024-11-03")),
                 (_d("2024-11-04"), _d("2024-11-04")), (_d("2024-11-05"), _d("2024-11-05")),
                 (_d("2024-11-06"), _d("2024-11-07"))),
            Fold("F2", (_d("2024-11-01"), _d("2024-11-03")), (_d("2024-11-04"), _d("2024-11-04")),
                 (_d("2024-11-05"), _d("2024-11-05")), (_d("2024-11-06"), _d("2024-11-07")),
                 (_d("2024-11-08"), _d("2024-11-09"))),
            Fold("F3", (_d("2024-11-01"), _d("2024-11-05")), (_d("2024-11-06"), _d("2024-11-06")),
                 (_d("2024-11-07"), _d("2024-11-08")), (_d("2024-11-09"), _d("2024-11-09")),
                 (_d("2024-11-10"), _d("2024-11-11"))),
            Fold("F4", (_d("2024-11-01"), _d("2024-11-07")), (_d("2024-11-08"), _d("2024-11-08")),
                 (_d("2024-11-09"), _d("2024-11-10")), (_d("2024-11-11"), _d("2024-11-11")),
                 (_d("2024-11-12"), _d("2024-11-13"))),
        ),
        percentile_bars=1440,
        rv_bars=240,
        r24_bars=240,
        hour_bars=60,
        sigma_hours=24,
        f6_window_ms=86_400_000,
        label="test-small",
    )


def synth_trades(days, *, seed: int = 7, active_share=0.92, trades_per_active_minute: float = 3.0,
                 day_active_minutes=None, day_trades=None, vol_bp_per_trade: float = 1.5,
                 mean_gap_run: float = 3.0, max_gap_min: int = 50) -> dict[str, np.ndarray]:
    """Random-walk prints on a minute grid with clustered empty-minute runs.

    day_active_minutes / day_trades (optional, per day) reproduce a per-day
    coverage profile (e.g. from inventory metadata); otherwise `active_share`."""
    rng = np.random.default_rng(seed)
    mts_parts, amt_parts = [], []
    for k, day in enumerate(days):
        start = day_ms(day)
        active_target = int(day_active_minutes[k]) if day_active_minutes is not None else int(round(1440 * active_share))
        active_target = max(1, min(1440, active_target))
        empty_target = 1440 - active_target
        active = np.ones(1440, dtype=bool)
        active[0] = True
        placed = 0
        guard = 0
        while placed < empty_target and guard < 100_000:
            guard += 1
            run = int(min(max_gap_min, max(1, rng.geometric(1.0 / mean_gap_run))))
            run = min(run, empty_target - placed)
            pos = int(rng.integers(1, 1440 - run))
            if not active[pos - 1] or not active[pos:pos + run].all():
                continue
            if pos + run < 1440 and not active[pos + run]:
                continue
            active[pos:pos + run] = False
            placed += run
        minutes = np.flatnonzero(active)
        if day_trades is not None:
            total = max(int(day_trades[k]), minutes.size)
            counts = 1 + rng.multinomial(total - minutes.size, np.full(minutes.size, 1.0 / minutes.size))
        else:
            counts = 1 + rng.poisson(max(trades_per_active_minute - 1.0, 0.0), minutes.size)
        offs = np.concatenate([np.sort(rng.integers(0, 60_000, c)) for c in counts])
        base = np.repeat(start + minutes.astype(np.int64) * 60_000, counts)
        mts_parts.append(base + offs)
        size = np.exp(rng.normal(-5.0, 1.5, offs.size))
        amt_parts.append(size * rng.choice([-1.0, 1.0], offs.size))
    mts = np.concatenate(mts_parts).astype(np.int64)
    order = np.argsort(mts, kind="mergesort")
    mts = mts[order]
    amount = np.concatenate(amt_parts)[order]
    steps = rng.standard_t(4, mts.size) * vol_bp_per_trade / 10000.0
    price = 60000.0 * np.exp(np.cumsum(steps))
    price = np.round(price, 1)
    return {"id": np.arange(1, mts.size + 1, dtype=np.int64), "mts": mts, "amount": amount, "price": price}
