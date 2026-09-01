"""Canonical, fail-closed qualification-horizon evidence.

This module translates observed paper tape and terminal economics into the
units consumed by lifecycle completion.  It deliberately does not infer a
gap-free horizon from elapsed wall time and does not turn absent costs into
zeroes.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


QUALIFICATION_HORIZON_SCHEMA = "lifecycle_qualification_horizon_v1"
CANONICAL_ECONOMICS_SCHEMA = "paper_terminal_economics_usd_v2"
ACTUAL_EXECUTION_GROSS_BASIS = "ACTUAL_EXECUTION_PRICES_INCLUDES_PRICE_IMPACT"
NET_SUBTRACTION_BASIS = "GROSS_MINUS_TRADING_FEES_MINUS_FUNDING_FEES"
CANONICAL_EXTREMA_SCHEMA = "paper_path_extrema_usd_v1"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def canonical_terminal_economics(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return cost-complete USD economics or explicit UNKNOWN blockers."""
    blockers: list[str] = []
    gross = _finite(source.get("gross_pnl_usd"))
    trading = _finite(source.get("trading_fees_usd"))
    funding = _finite(source.get("funding_fees_usd"))
    latency = _finite(source.get("latency_cost_usd"))
    net = _finite(source.get("net_pnl_usd"))

    combined_slippage = _finite(source.get("slippage_cost_usd"))
    entry_slippage = _finite(source.get("entry_slippage_cost_usd"))
    exit_slippage = _finite(source.get("exit_slippage_cost_usd"))
    if combined_slippage is None:
        if entry_slippage is None:
            blockers.append("ENTRY_SLIPPAGE_COST_USD_MISSING")
        if exit_slippage is None:
            blockers.append("EXIT_SLIPPAGE_COST_USD_MISSING")
        if entry_slippage is not None and exit_slippage is not None:
            combined_slippage = entry_slippage + exit_slippage

    for name, value in (
        ("GROSS_PNL_USD", gross),
        ("TRADING_FEES_USD", trading),
        ("FUNDING_FEES_USD", funding),
        ("LATENCY_COST_USD", latency),
        ("NET_PNL_USD", net),
    ):
        if value is None:
            blockers.append(f"{name}_MISSING")
    if combined_slippage is None:
        blockers.append("SLIPPAGE_COST_USD_MISSING")
    values = {
        "gross_pnl_usd": gross,
        "trading_fees_usd": trading,
        "funding_fees_usd": funding,
        "slippage_cost_usd": combined_slippage,
        "latency_cost_usd": latency,
        "net_pnl_usd": net,
    }
    if not blockers:
        # Gross PnL is calculated from the actual entry and exit executions.
        # Price impact, slippage and latency effects are therefore already in
        # gross.  They remain mandatory causal attribution, but subtracting
        # them here would charge the same execution cost twice.
        expected = gross - trading - funding
        if abs(expected - net) > 1e-8:
            blockers.append("NET_PNL_UNRECONCILED")
    return {
        "schema": CANONICAL_ECONOMICS_SCHEMA,
        "status": "COMPLETE" if not blockers else "UNKNOWN",
        "unit": "USD",
        "gross_pnl_basis": ACTUAL_EXECUTION_GROSS_BASIS,
        "net_pnl_reconciliation_basis": NET_SUBTRACTION_BASIS,
        "separately_subtracted_from_gross": ["trading_fees_usd", "funding_fees_usd"],
        "attribution_only_not_subtracted": ["slippage_cost_usd", "latency_cost_usd"],
        **values,
        "blockers": sorted(set(blockers)),
    }


def canonical_path_extrema_usd(
    path_extrema: Mapping[str, Any], *, entry_price: Any, filled_quantity: Any,
) -> dict[str, Any]:
    """Convert observed percentage extrema to USD using exact entry notional."""
    blockers: list[str] = []
    entry = _finite(entry_price)
    quantity = _finite(filled_quantity)
    mfe_pct = _finite(path_extrema.get("mfe_pct"))
    mae_pct = _finite(path_extrema.get("mae_pct"))
    if entry is None or entry <= 0:
        blockers.append("ENTRY_PRICE_MISSING")
    if quantity is None or quantity <= 0:
        blockers.append("FILLED_QUANTITY_MISSING")
    if mfe_pct is None:
        blockers.append("MFE_PCT_MISSING")
    if mae_pct is None:
        blockers.append("MAE_PCT_MISSING")
    notional = entry * quantity if entry and quantity and entry > 0 and quantity > 0 else None
    return {
        "schema": CANONICAL_EXTREMA_SCHEMA,
        "status": "COMPLETE" if not blockers else "UNKNOWN",
        "source_basis": path_extrema.get("basis") or "UNAVAILABLE",
        "source_unit": "PERCENT_OF_ENTRY_NOTIONAL",
        "unit": "USD",
        "entry_notional_usd": notional,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "mfe_usd": (notional * mfe_pct / 100.0) if notional is not None and mfe_pct is not None else None,
        "mae_usd": (notional * mae_pct / 100.0) if notional is not None and mae_pct is not None else None,
        "blockers": sorted(set(blockers)),
    }


def qualification_post_observation(
    coverage: Mapping[str, Any], *, terminal_ts: Any,
    lifecycle_horizon_sec: float = 7200.0, max_gap_sec: float = 2.0,
) -> dict[str, Any]:
    """Prove a bounded post-terminal tape horizon; elapsed time is insufficient."""
    blockers: list[str] = []
    terminal = _finite(terminal_ts)
    requested_start = _finite(coverage.get("requested_start_ts"))
    requested_end = _finite(coverage.get("requested_end_ts"))
    observed_start = _finite(coverage.get("observed_start_ts"))
    observed_end = _finite(coverage.get("observed_end_ts"))
    observed_gap = _finite(coverage.get("max_gap_sec"))
    required_end = terminal + max(0.0, float(lifecycle_horizon_sec)) if terminal is not None else None
    if terminal is None or terminal <= 0:
        blockers.append("TERMINAL_TS_MISSING")
    if requested_start is None or terminal is None or requested_start > terminal:
        blockers.append("POST_OBSERVATION_START_NOT_BOUND")
    if requested_end is None or required_end is None or requested_end < required_end:
        blockers.append("POST_OBSERVATION_REQUESTED_HORIZON_SHORT")
    if observed_start is None or terminal is None or observed_start > terminal + max_gap_sec:
        blockers.append("POST_OBSERVATION_START_MISSING")
    if observed_end is None or required_end is None or observed_end < required_end - max_gap_sec:
        blockers.append("POST_OBSERVATION_END_MISSING")
    if coverage.get("requested_bounds_complete") is not True:
        blockers.append("POST_OBSERVATION_BOUNDS_INCOMPLETE")
    if coverage.get("two_second_or_better") is not True:
        blockers.append("POST_OBSERVATION_CADENCE_INCOMPLETE")
    if observed_gap is not None and observed_gap > max_gap_sec:
        blockers.append("POST_OBSERVATION_GAP_PRESENT")
    for field in (
        "parse_errors", "invalid_timestamp_rows", "invalid_price_rows",
        "invalid_bbo_rows", "invalid_depth_rows",
    ):
        if coverage.get(field) != 0:
            blockers.append(f"POST_OBSERVATION_{field.upper()}")
    if coverage.get("all_rows_have_valid_bbo") is not True:
        blockers.append("POST_OBSERVATION_BBO_INCOMPLETE")
    if coverage.get("all_rows_have_visible_depth") is not True:
        blockers.append("POST_OBSERVATION_DEPTH_INCOMPLETE")
    blockers = sorted(set(blockers))
    return {
        "schema": QUALIFICATION_HORIZON_SCHEMA,
        "complete": not blockers,
        "gaps_absent": not blockers,
        "terminal_ts": terminal,
        "required_horizon_sec": float(lifecycle_horizon_sec),
        "required_complete_through_ts": required_end,
        "complete_through_ts": observed_end if not blockers else None,
        "max_allowed_gap_sec": float(max_gap_sec),
        "observed_max_gap_sec": observed_gap,
        "bbo_depth_required": True,
        "blockers": blockers,
    }
