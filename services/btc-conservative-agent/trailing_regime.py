"""Causal (trailing-window) volatility regime thresholds.

Full-sample quantiles leak the future distribution into any walk-forward fold
that uses them as filters. Every regime label here is computed only from
observations strictly before the labelled one: the percentile rank of the
current value inside a rolling window of past values, with an explicit WARMUP
label until enough history exists. The collector stamps these labels at
collection time and the analyzer recomputes them with the same function, so a
fold can never see a threshold derived from its own future.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from typing import Iterable, Optional, Sequence

SCHEMA = "trailing_regime_v1"
DEFAULT_WINDOW = 7 * 1440          # one week of 1-minute observations
DEFAULT_MIN_HISTORY = 1440         # one day before any label is emitted
CALM_PCT = 40.0
EXTREME_PCT = 90.0
WARMUP, CALM, NORMAL, EXTREME = "WARMUP", "CALM", "NORMAL", "EXTREME"


def label_for_rank(rank_pct: Optional[float], *, calm_pct: float = CALM_PCT,
                   extreme_pct: float = EXTREME_PCT) -> str:
    if rank_pct is None:
        return WARMUP
    if rank_pct > extreme_pct:
        return EXTREME
    if rank_pct < calm_pct:
        return CALM
    return NORMAL


class TrailingPercentile:
    """Rolling-window percentile rank of each new value against the past only."""

    def __init__(self, window: int = DEFAULT_WINDOW, min_history: int = DEFAULT_MIN_HISTORY) -> None:
        self.window = int(window)
        self.min_history = int(min_history)
        self._fifo: deque = deque()
        self._sorted: list = []

    def __len__(self) -> int:
        return len(self._fifo)

    def rank(self, value: Optional[float]) -> Optional[float]:
        """Percentile rank (0-100) of ``value`` among the stored past values."""
        if value is None or not math.isfinite(value) or len(self._sorted) < self.min_history:
            return None
        lo = bisect.bisect_left(self._sorted, value)
        hi = bisect.bisect_right(self._sorted, value)
        return round(100.0 * (lo + hi) / 2.0 / len(self._sorted), 3)

    def push(self, value: Optional[float]) -> None:
        if value is None or not math.isfinite(value):
            return
        self._fifo.append(value)
        bisect.insort(self._sorted, value)
        if len(self._fifo) > self.window:
            old = self._fifo.popleft()
            idx = bisect.bisect_left(self._sorted, old)
            if idx < len(self._sorted) and self._sorted[idx] == old:
                self._sorted.pop(idx)

    def observe(self, value: Optional[float]) -> dict:
        """Rank and label ``value`` against the past, then add it to the history."""
        rank = self.rank(value)
        out = {"rank_pct": rank, "label": label_for_rank(rank), "history_n": len(self._sorted)}
        self.push(value)
        return out


def trailing_labels(values: Sequence[Optional[float]], *, window: int = DEFAULT_WINDOW,
                    min_history: int = DEFAULT_MIN_HISTORY) -> list:
    """Causal labels for an already time-ordered series (analyzer use)."""
    tp = TrailingPercentile(window=window, min_history=min_history)
    return [tp.observe(v) for v in values]


def rv_bps(log_returns: Iterable[Optional[float]]) -> Optional[float]:
    """Sample standard deviation of log returns, in basis points."""
    vals = [r for r in log_returns if r is not None and math.isfinite(r)]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    var = sum((r - mean) ** 2 for r in vals) / (len(vals) - 1)
    return round(math.sqrt(var) * 1e4, 4)
