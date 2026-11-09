"""EXT2 W1 exit lattice. Paper only. Live is off.

combos_searched is the closed-form grammar count, 181442. The CPU kernel runs
only when allow_cpu_fallback is passed True (parity tests). The default is
False: no CUDA means STOP, and a real run must not take the CPU path.

Liquidation at 100x is checked on each trade print before the hard stop.
A gap through both books the print price plus friction and stays in P&L.
Fold censor boundaries are a list, one at each fold-test end. Trailing
statistics that are short of their window take no entry and are counted.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import numpy as np

from research.ext2_costs import (
    FEE_BP,
    FUNDING,
    LIQUIDATION_ADVERSE_BP,
    Ext2Stop,
    friction_bp_per_side,
    net_bp,
    utc_ms,
)
from research.ext2_rules import liquidation_fill_price

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
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        raise Ext2Stop("STOP_GPU_MISSING") from exc


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


def feature_log_ret_bp(closes: Sequence[float], index: int, lookback: int) -> float | None:
    """Log return over exactly `lookback` bars. A short history is undefined."""
    if lookback != FEATURE_LOOKBACK_MIN and lookback not in LOOKBACKS_MIN:
        raise Ext2Stop("STOP_LOOKBACK")
    if index < lookback:
        return None
    previous = float(closes[index - lookback])
    current = float(closes[index])
    if previous <= 0.0 or current <= 0.0:
        return None
    return math.log(current / previous) * 10000.0


def trailing_sigma(values: Sequence[float], required: int) -> float | None:
    array = np.asarray(list(values), dtype=np.float64)
    if array.shape != (required,) or not np.isfinite(array).all():
        return None
    return float(array.std(ddof=0))


def trailing_percentile(values: Sequence[float], required: int, probability: float) -> float | None:
    array = np.asarray(list(values), dtype=np.float64)
    if array.shape != (required,) or not np.isfinite(array).all():
        return None
    return float(np.quantile(array, probability / 100.0))


def bar_takes_entry(*, feature_bp: float | None, sigma: float | None, percentile: float | None) -> bool:
    return feature_bp is not None and sigma is not None and percentile is not None


def screen_entries(closes: Sequence[float], *, lookback: int = FEATURE_LOOKBACK_MIN) -> dict[str, int]:
    undefined = 0
    entries = 0
    for index in range(len(closes)):
        feature = feature_log_ret_bp(closes, index, lookback)
        sigma = None
        percentile = None
        if index >= SIGMA_DAYS * BARS_PER_DAY:
            sigma = trailing_sigma(closes[index - SIGMA_DAYS * BARS_PER_DAY:index], SIGMA_DAYS * BARS_PER_DAY)
        prior = index - lookback
        need = PERCENTILE_DAYS * BARS_PER_DAY
        if feature is not None and prior >= need:
            window = [feature_log_ret_bp(closes, cursor, lookback) for cursor in range(index - need, index)]
            if all(value is not None for value in window):
                percentile = trailing_percentile([float(value) for value in window if value is not None], need, 90.0)
        if bar_takes_entry(feature_bp=feature, sigma=sigma, percentile=percentile):
            entries += 1
        else:
            undefined += 1
    return {"undefined_stat_bars": undefined, "entries": entries, "bars": len(closes)}


def features_from_trade_bars(bars: Sequence[dict[str, Any]]) -> list[dict[str, float]]:
    """Trade-bar closes only. Any other source is refused."""
    out = []
    for bar in bars:
        if bar.get("source") != "trades":
            raise Ext2Stop("STOP_FEATURE_SOURCE")
        out.append({"close": float(bar["close"]), "trade_count": float(bar["trade_count"])})
    if len(out) >= 2:
        previous = out[0]["close"]
        for row, bar in zip(out, bars):
            row["ret_bp"] = math.log(row["close"] / previous) * 10000.0 if previous > 0.0 else 0.0
            previous = float(bar["close"])
        out[0]["ret_bp"] = 0.0
    return out


def _exit_table() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    take, hold, thesis, stop = np.meshgrid(
        np.asarray(TAKE_PROFIT_BP, dtype=np.float64),
        np.asarray(TIME_STOP_MIN, dtype=np.float64),
        np.asarray(THESIS_CUT, dtype=np.float64),
        np.asarray(HARD_STOP_BP, dtype=np.float64),
        indexing="ij",
    )
    return take.reshape(-1), hold.reshape(-1), thesis.reshape(-1), stop.reshape(-1)


def censor_prints(print_mts: np.ndarray, print_price: np.ndarray, thesis: np.ndarray, *,
                  entry_mts: int, boundary: int | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Prints at or after the active fold boundary are not returned."""
    if boundary is None:
        keep = np.zeros(print_mts.shape, dtype=bool)
    else:
        keep = (print_mts > int(entry_mts)) & (print_mts < int(boundary))
    return print_mts[keep], print_price[keep], thesis[keep]


def liquidation_price(entry_price: float, side: float) -> float:
    """Derived 100x liquidation level. The crossing print is only the detector."""
    return float(entry_price) * (1.0 - float(side) * LIQUIDATION_ADVERSE_BP / 10000.0)


def _first_index(mask: np.ndarray) -> np.ndarray:
    width = mask.shape[0]
    if mask.ndim == 1:
        if not bool(mask.any()):
            return np.asarray(width)
        return np.asarray(int(np.argmax(mask)))
    found = mask.any(axis=0)
    index = np.argmax(mask, axis=0)
    return np.where(found, index, width)


def _apply_priority(indexes: list[np.ndarray], codes: list[int], width: int) -> tuple[np.ndarray, np.ndarray]:
    best = np.full(indexes[0].shape, width + 1)
    code = np.zeros(indexes[0].shape, dtype=np.int64)
    for idx, reason in zip(indexes, codes):
        better = idx < best
        best = np.where(better, idx, best)
        code = np.where(better, reason, code)
    return best, code


def _finish_scan(code: np.ndarray, exit_px: np.ndarray, *, side: float, entry_price: float,
                 friction: float, mts: np.ndarray) -> dict[str, Any]:
    from research.ext2_costs import stress_friction_bp_per_side, tick_adverse_bp
    gross = np.full(code.shape, np.nan)
    nets = np.full(code.shape, np.nan)
    stress = np.full(code.shape, np.nan)
    ticked = np.full(code.shape, np.nan)
    kept = code != 7
    if np.any(kept):
        gross[kept] = float(side) * (exit_px[kept] / float(entry_price) - 1.0) * 10000.0
        nets[kept] = gross[kept] - friction - friction
        stress_side = stress_friction_bp_per_side(0.0 if friction <= 1.0 else friction - 0.5)
        # friction passed in is already max(1, roll+0.5); 1.5x that per side
        stress[kept] = gross[kept] - 1.5 * friction - 1.5 * friction
        tick = tick_adverse_bp(entry_price) + tick_adverse_bp(float(np.nanmean(exit_px[kept])))
        ticked[kept] = nets[kept] - tick
    return {
        "reason_code": code,
        "exit_price": exit_px,
        "gross_bp": gross,
        "net_bp": nets,
        "net_stress_bp": stress,
        "net_tick_bp": ticked,
        "print_mts_read_max": None if mts.size == 0 else int(mts.max()),
        "liquidation_count": int(np.sum(code == 5)),
        "dropped_count": int(np.sum(code == 7)),
        "path_end_count": int(np.sum(code == 8)),
        "censored_count": int(np.sum(code == 6)),
    }


def _scan_numpy(price: np.ndarray, mts: np.ndarray, thesis: np.ndarray, *, entry_price: float,
                entry_mts: int, side: float, friction: float, mode: str) -> dict[str, Any]:
    """Vectorised over the 54 exits. No per-print Python loop."""
    take, hold_min, thesis_on, stop = _exit_table()
    width = int(take.size)
    code = np.zeros(width, dtype=np.int64)
    exit_px = np.full(width, np.nan)
    if price.size == 0 or mode == "drop" and price.size == 0:
        code[:] = 7
        return _finish_scan(code, exit_px, side=side, entry_price=entry_price, friction=friction, mts=mts)
    gross = float(side) * (price / float(entry_price) - 1.0) * 10000.0
    elapsed = mts.astype(np.float64) - float(entry_mts)
    level = liquidation_price(entry_price, side)
    liquidated = price <= level if side > 0 else price >= level
    stop_hit = gross[:, None] <= -stop[None, :]
    take_hit = gross[:, None] >= take[None, :]
    thesis_hit = (thesis_on[None, :] > 0.0) & thesis[:, None]
    time_hit = elapsed[:, None] >= hold_min[None, :] * 60_000.0
    liq_i = np.full(width, _first_index(liquidated))
    best, code = _apply_priority(
        [liq_i, _first_index(stop_hit), _first_index(take_hit), _first_index(thesis_hit), _first_index(time_hit)],
        [5, 4, 3, 2, 1],
        price.size,
    )
    no_hit = best > price.size - 1
    if mode == "drop":
        code = np.where(no_hit, 7, code)
    elif mode == "force_close":
        code = np.where(no_hit, 6, code)
        best = np.where(no_hit, price.size - 1, best)
    else:
        code = np.where(no_hit, 8, code)
        best = np.where(no_hit, price.size - 1, best)
    printable = code != 7
    chosen = np.clip(best, 0, max(price.size - 1, 0)).astype(np.int64)
    exit_px = np.where(printable, price[chosen], np.nan)
    if np.any(code == 5):
        worse = np.array([
            liquidation_fill_price(entry_price, side, float(price[int(chosen[i])])) if code[i] == 5 else np.nan
            for i in range(code.size)
        ])
        exit_px = np.where(code == 5, worse, exit_px)
    return _finish_scan(code, exit_px.astype(np.float64), side=side, entry_price=entry_price, friction=friction, mts=mts)


def _scan_torch(price: np.ndarray, mts: np.ndarray, thesis: np.ndarray, *, entry_price: float,
                entry_mts: int, side: float, friction: float, mode: str, device: str) -> dict[str, Any]:
    """Same lattice on a torch device, vectorised across prints and the 54 exits."""
    import torch

    take_np, hold_np, thesis_np, stop_np = _exit_table()
    dev = torch.device(device)
    price_t = torch.as_tensor(price, dtype=torch.float64, device=dev)
    mts_t = torch.as_tensor(mts, dtype=torch.float64, device=dev)
    thesis_t = torch.as_tensor(thesis.astype(np.bool_), dtype=torch.bool, device=dev)
    take = torch.as_tensor(take_np, dtype=torch.float64, device=dev)
    hold_min = torch.as_tensor(hold_np, dtype=torch.float64, device=dev)
    thesis_on = torch.as_tensor(thesis_np, dtype=torch.float64, device=dev)
    stop = torch.as_tensor(stop_np, dtype=torch.float64, device=dev)
    width = int(take.shape[0])
    if price_t.numel() == 0:
        return _scan_numpy(price, mts, thesis, entry_price=entry_price, entry_mts=entry_mts, side=side, friction=friction, mode=mode)
    gross = float(side) * (price_t / float(entry_price) - 1.0) * 10000.0
    elapsed = mts_t - float(entry_mts)
    level = liquidation_price(entry_price, side)
    liquidated = price_t <= level if side > 0 else price_t >= level

    def first(mask: torch.Tensor) -> torch.Tensor:
        found = mask.any(dim=0)
        index = mask.to(torch.int64).argmax(dim=0)
        return torch.where(found, index, torch.full_like(index, price_t.shape[0]))

    liq_i = torch.full((width,), int(liquidated.to(torch.int64).argmax()) if bool(liquidated.any()) else price_t.shape[0], device=dev)
    best = torch.full((width,), price_t.shape[0] + 1, device=dev)
    code = torch.zeros(width, dtype=torch.int64, device=dev)
    for idx, reason in (
        (liq_i, 5),
        (first(gross[:, None] <= -stop[None, :]), 4),
        (first(gross[:, None] >= take[None, :]), 3),
        (first((thesis_on[None, :] > 0) & thesis_t[:, None]), 2),
        (first(elapsed[:, None] >= hold_min[None, :] * 60_000.0), 1),
    ):
        better = idx < best
        best = torch.where(better, idx, best)
        code = torch.where(better, torch.full_like(code, reason), code)
    no_hit = best > price_t.shape[0] - 1
    if mode == "drop":
        code = torch.where(no_hit, torch.full_like(code, 7), code)
    elif mode == "force_close":
        code = torch.where(no_hit, torch.full_like(code, 6), code)
        best = torch.where(no_hit, torch.full_like(best, price_t.shape[0] - 1), best)
    else:
        code = torch.where(no_hit, torch.full_like(code, 8), code)
        best = torch.where(no_hit, torch.full_like(best, price_t.shape[0] - 1), best)
    chosen = best.clamp(0, price_t.shape[0] - 1)
    exit_px = price_t[chosen]
    if bool((code == 5).any()):
        prints = exit_px.detach().cpu().numpy()
        codes = code.detach().cpu().numpy()
        worse = np.array([
            liquidation_fill_price(entry_price, side, float(prints[i])) if codes[i] == 5 else np.nan
            for i in range(codes.size)
        ])
        exit_px = torch.as_tensor(np.where(codes == 5, worse, prints), dtype=torch.float64, device=dev)
    exit_px = torch.where(code == 7, torch.full_like(exit_px, float("nan")), exit_px)
    return _finish_scan(
        code.detach().cpu().numpy(), exit_px.detach().cpu().numpy(),
        side=side, entry_price=entry_price, friction=friction, mts=mts,
    )


def _one_event(price, mts, thesis, *, entry_price, entry_mts, side, friction, mode, kernel, device):
    if kernel == "torch":
        return _scan_torch(
            price, mts, thesis, entry_price=entry_price, entry_mts=entry_mts, side=side,
            friction=friction, mode=mode, device=device,
        )
    return _scan_numpy(
        price, mts, thesis, entry_price=entry_price, entry_mts=entry_mts, side=side,
        friction=friction, mode=mode,
    )


def evaluate_exit_lattice(events: Sequence[dict[str, Any]], *, roll_half_spread_bp: float = 0.0,
                          censor_boundaries: Sequence[int] | None = None,
                          embargo_mode: str = "force_close", allow_cpu_fallback: bool = False,
                          kernel: str = "numpy") -> dict[str, Any]:
    """Score the 54-exit lattice. Default refuses to run without CUDA."""
    if embargo_mode not in {"force_close", "drop", "path_end"}:
        raise Ext2Stop("STOP_EMBARGO_MODE")
    if not allow_cpu_fallback:
        if not _cuda_available():
            raise Ext2Stop("STOP_GPU_MISSING")
        kernel = "torch"
        device = "cuda"
    else:
        device = "cpu"
        if kernel not in {"numpy", "torch"}:
            raise Ext2Stop("STOP_KERNEL")
    boundaries = tuple(default_censor_boundaries_ms() if censor_boundaries is None else censor_boundaries)
    friction = friction_bp_per_side(roll_half_spread_bp)
    cells = []
    liquidation_count = 0
    dropped_count = 0
    path_end_count = 0
    censored_count = 0
    for event in events:
        boundary = active_censor_mts(int(event["entry_mts"]), boundaries)
        raw_mts = np.asarray(event["print_mts"], dtype=np.int64)
        mts, price, thesis = censor_prints(
            raw_mts,
            np.asarray(event["print_price"], dtype=np.float64),
            np.asarray(event.get("thesis_cross", [False] * len(raw_mts)), dtype=bool),
            entry_mts=int(event["entry_mts"]),
            boundary=boundary,
        )
        if mts.size and boundary is not None and int(mts.max()) >= int(boundary):
            raise Ext2Stop("STOP_CENSOR")
        truncated = boundary is None or bool(np.any(raw_mts >= int(boundary)))
        scanned = _one_event(
            price, mts, thesis, entry_price=float(event["entry_price"]), entry_mts=int(event["entry_mts"]),
            side=float(event["side"]), friction=friction,
            mode=embargo_mode if truncated else "path_end",
            kernel=kernel, device=device,
        )
        if boundary is not None and scanned["print_mts_read_max"] is not None and scanned["print_mts_read_max"] >= boundary:
            raise Ext2Stop("STOP_CENSOR")
        liquidation_count += scanned["liquidation_count"]
        dropped_count += scanned["dropped_count"]
        path_end_count += scanned["path_end_count"]
        censored_count += scanned["censored_count"]
        cells.append({
            "boundary_mts": boundary,
            "reasons": [REASON.get(int(code), "NONE") for code in scanned["reason_code"]],
            "exit_price": [None if not math.isfinite(float(value)) else float(value) for value in scanned["exit_price"]],
            "gross_bp": [None if not math.isfinite(float(value)) else float(value) for value in scanned["gross_bp"]],
            "net_bp": [None if not math.isfinite(float(value)) else float(value) for value in scanned["net_bp"]],
            "print_mts_read_max": scanned["print_mts_read_max"],
            "liquidation_count": scanned["liquidation_count"],
            "path_end_count": scanned["path_end_count"],
            "censored_count": scanned["censored_count"],
        })
    device_name = "gpu" if device == "cuda" else "cpu"
    return {
        "schema": "ext2_exit_lattice_v1",
        "paper_only": PAPER_ONLY,
        "live": LIVE_ENABLED,
        "edge_claim": False,
        "combos_searched": count_combos(),
        "exit_grid_n": EXIT_GRID_N,
        "events": len(events),
        "lattice_evaluated": len(events) * EXIT_GRID_N,
        "liquidation_count": liquidation_count,
        "dropped_count": dropped_count,
        "path_end_count": path_end_count,
        "censored_close_count": censored_count,
        "costs": {"fee_bp": FEE_BP, "funding": FUNDING, "friction_bp_per_side": friction, "funding_in_net": False},
        "kernel": "torch" if kernel == "torch" else "numpy",
        "device": device_name,
        "fallback": bool(allow_cpu_fallback and device_name == "cpu"),
        "parity_test": bool(allow_cpu_fallback),
        "parity_atol_bp": PARITY_ATOL,
        "censor_boundaries_mts": list(boundaries),
        "cells": cells,
    }


def fold_gate_inputs(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Liquidations stay in the fold net. They are not dropped from the gate."""
    nets = []
    liquidations = 0
    for trade in trades:
        if trade.get("reason") == "DROPPED":
            continue
        if "net_bp" not in trade:
            raise Ext2Stop("STOP_FOLD")
        nets.append(float(trade["net_bp"]))
        if trade.get("reason") == "LIQUIDATION":
            liquidations += 1
    total = float(sum(nets))
    return {
        "net_bp": total,
        "trades": len(nets),
        "liquidation_count": liquidations,
        "liquidations_excluded": False,
        "fee_bp": FEE_BP,
        "funding": FUNDING,
        "passes_net_gate": total > 0.0,
    }


def gross_bp(side: float, entry_price: float, exit_price: float) -> float:
    return float(side) * (float(exit_price) / float(entry_price) - 1.0) * 10000.0


def liquidation_net_bp(side: float, entry_price: float, exit_price: float, roll_half_spread_bp: float = 0.0) -> float:
    return net_bp(gross_bp(side, entry_price, exit_price), roll_half_spread_bp)


def parse_boundary(text: str) -> int:
    datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    return utc_ms(text)
