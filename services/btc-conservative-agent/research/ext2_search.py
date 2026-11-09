"""EXT2 W1 grammar constants, the 181,442 count and fold censor boundaries.

Paper only. Live is off. The search itself lives in ext2_pipeline (features,
chains, gates) and ext2_lattice (batched first-passage exit lattice). The old
per-event scan that lived here was removed in push 3 (HM B12).
"""
from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import datetime, timezone

import numpy as np

from research.ext2_costs import Ext2Stop, net_bp, utc_ms

COMBOS_SEARCHED = 181_442
PARITY_ATOL = 1e-9
PAPER_ONLY = True
LIVE_ENABLED = False
FEATURES = ("ret_L", "flow_imb_L", "intensity_L", "rv_break_L", "vwap_dev_L", "large_print_L")
LOOKBACKS_MIN = (5, 15, 60, 240)
PERCENTILES = (80.0, 90.0, 95.0, 97.5, 99.0)
MODES = ("follow", "fade")
REGIMES = ("NONE", "BULL", "BEAR", "SIDE")
TAKE_PROFIT_BP = (20.0, 40.0, 80.0)
TIME_STOP_MIN = (30, 120, 480)
THESIS_CUT = (0, 1)
HARD_STOP_BP = (15.0, 30.0, 40.0)
SIGMA_DAYS = 29
PERCENTILE_DAYS = 30
FEATURE_LOOKBACK_MIN = 240
BARS_PER_DAY = 1440
EXIT_GRID_N = 54
FOLD_CENSOR_ISO = (
    "2024-12-28T00:00:00Z",
    "2025-01-08T00:00:00Z",
    "2025-01-19T00:00:00Z",
    "2025-01-29T00:00:00Z",
)
REASON = {
    1: "TIME",
    2: "THESIS",
    3: "TAKE_PROFIT",
    4: "HARD_STOP",
    5: "LIQUIDATION",
    6: "EMBARGO_FORCE_CLOSE",
    7: "DROPPED",
    8: "PATH_END",
}


def _cuda_available() -> bool:
    from research.ext2_lattice import cuda_available
    return cuda_available()


def count_combos_closed_form() -> int:
    exits = len(TAKE_PROFIT_BP) * len(TIME_STOP_MIN) * len(THESIS_CUT) * len(HARD_STOP_BP)
    pairs = len(FEATURES) * (len(FEATURES) - 1) // 2
    return (len(FEATURES) + pairs) * len(LOOKBACKS_MIN) * len(PERCENTILES) * len(MODES) * len(REGIMES) * exits + 2


def count_combos_enumerated() -> int:
    members = len(FEATURES) + len(list(itertools.combinations(FEATURES, 2)))
    exits = len(list(itertools.product(TAKE_PROFIT_BP, TIME_STOP_MIN, THESIS_CUT, HARD_STOP_BP)))
    counted = 0
    for _members in range(members):
        for _lookback in LOOKBACKS_MIN:
            for _percentile in PERCENTILES:
                for _mode in MODES:
                    for _regime in REGIMES:
                        counted += exits
    return counted + 2


def count_combos() -> int:
    closed = count_combos_closed_form()
    enumerated = count_combos_enumerated()
    if closed != COMBOS_SEARCHED or enumerated != COMBOS_SEARCHED:
        raise Ext2Stop("STOP_COUNT")
    return COMBOS_SEARCHED


def default_censor_boundaries_ms() -> tuple[int, ...]:
    return tuple(utc_ms(text) for text in FOLD_CENSOR_ISO)


def active_censor_mts(entry_mts: int, boundaries: Sequence[int]) -> int | None:
    later = [int(boundary) for boundary in boundaries if int(entry_mts) < int(boundary)]
    return min(later) if later else None


def first_eligible_bar() -> int:
    """First bar index whose 240-minute feature and 30-day percentile can exist.

    Sigma needs 29 days. The percentile window starts only after the full
    feature lookback, so it is not filled with a shorter return.
    """
    percentile_index = FEATURE_LOOKBACK_MIN + PERCENTILE_DAYS * BARS_PER_DAY
    sigma_index = SIGMA_DAYS * BARS_PER_DAY
    return max(percentile_index, sigma_index)


def history_undefined(index: int) -> bool:
    if isinstance(index, bool) or not isinstance(index, (int, np.integer)) or int(index) < 0:
        raise Ext2Stop("STOP_BAR")
    return int(index) < first_eligible_bar()


def undefined_stat_bar_count(n_bars: int) -> int:
    if isinstance(n_bars, bool) or not isinstance(n_bars, (int, np.integer)) or int(n_bars) < 0:
        raise Ext2Stop("STOP_BAR")
    return min(int(n_bars), first_eligible_bar())


def gross_bp(side: float, entry_price: float, exit_price: float) -> float:
    return float(side) * (float(exit_price) / float(entry_price) - 1.0) * 10000.0


def liquidation_net_bp(side: float, entry_price: float, exit_price: float, roll_half_spread_bp: float = 0.0) -> float:
    return net_bp(gross_bp(side, entry_price, exit_price), roll_half_spread_bp)


def parse_boundary(text: str) -> int:
    datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    return utc_ms(text)
