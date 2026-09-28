"""Bounded pre-AI research scenario capture; no I/O and no trading authority."""
from copy import deepcopy
from datetime import datetime
from math import isfinite
from collections.abc import Mapping

from research.quantity_execution import validate_signed_quantity_constraints


def build_runtime_baseline_declaration(*, context, quantity_capture, symbol,
        source_revision, captured_at_ts, margin_usd, leverage, maker_fee_rate,
        taker_fee_rate, sampling_interval_sec=1, required_horizon_sec=7200):
    """Return explicit unsupported reasons rather than invent causal inputs.

    ATR captured_ts is the original indicator calculation observation, while
    available_at_ts is this runtime read. Neither is backdated to signal time.
    Fees are a fixed initial-notional scenario, not measured terminal costs.
    """
    reasons = []
    def number(value, label, positive=True):
        try:
            result = float(value)
            if isinstance(value, bool) or not isfinite(result) or (result <= 0 if positive else result < 0):
                raise ValueError()
            return result
        except (TypeError, ValueError):
            reasons.append(label)
            return None
    now = number(captured_at_ts, "DECLARATION_TIME_INVALID")
    margin = number(margin_usd, "CONFIGURED_MARGIN_UNAVAILABLE")
    lev = number(leverage, "CONFIGURED_LEVERAGE_UNAVAILABLE")
    maker = number(maker_fee_rate, "CONFIGURED_MAKER_FEE_UNAVAILABLE", False)
    taker = number(taker_fee_rate, "CONFIGURED_TAKER_FEE_UNAVAILABLE", False)
    receipt = quantity_capture.get("receipt") if isinstance(quantity_capture, Mapping) else None
    constraints, defects = validate_signed_quantity_constraints(receipt, symbol=symbol)
    reasons.extend(defects)
    if constraints:
        if constraints.get("source_revision") != source_revision:
            reasons.append("QUANTITY_REVISION_MISMATCH")
        try:
            stamp = datetime.fromisoformat(constraints["captured_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or now is None or stamp.timestamp() > now:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            reasons.append("QUANTITY_OBSERVATION_NOT_CAUSAL")
    context = context if isinstance(context, Mapping) else {}
    cycle = context.get("cycle_3m_universe") or context.get("exhaustion_3m") or {}
    cycle = cycle if isinstance(cycle, Mapping) else {}
    atr = number(cycle.get("atr14_pct_3m"), "ATR_VALUE_UNAVAILABLE")
    observed = number(cycle.get("captured_ts"), "ATR_SOURCE_OBSERVATION_TIME_UNAVAILABLE")
    if observed is not None and now is not None and observed > now:
        reasons.append("ATR_SOURCE_OBSERVATION_IN_FUTURE")
    if cycle.get("calculation_status") == "UNKNOWN" or cycle.get("evidence_status") in {"SOURCE_STALE", "SOURCE_UNAVAILABLE"}:
        reasons.append("ATR_SOURCE_UNAVAILABLE_OR_STALE")
    horizon = number(required_horizon_sec, "COVERAGE_HORIZON_INVALID")
    if (isinstance(sampling_interval_sec, bool) or sampling_interval_sec not in (1, 2)
            or horizon is None or horizon > 50000 * sampling_interval_sec):
        reasons.append("COVERAGE_POLICY_INVALID")
    if reasons:
        return {"status": "UNSUPPORTED", "reasons": sorted(set(reasons)), "declaration": None}
    declaration = {
        "schema": "research_baseline_context_declaration_v1",
        "evidence_basis": "DECLARED_SIMULATION", "declared_at_ts": now,
        "provenance": "RUNTIME_PRE_AI_CONFIG_AND_CACHED_VENUE_METADATA_V1",
        "sizing_mode": "FIXED_MARGIN", "margin_usd": margin, "leverage": lev,
        "signed_quantity_constraints": deepcopy(receipt),
        "input_latency_sec": 0, "latency_basis": "ZERO_ADDITIONAL_LATENCY_DIAGNOSTIC_ONLY",
        "input_fee_assumption_usd": margin * lev * (maker + taker),
        "fee_basis": "DECLARED_ROUND_TRIP_INITIAL_NOTIONAL_ESTIMATE_NOT_MEASURED",
        "configured_maker_fee_rate": maker, "configured_taker_fee_rate": taker,
        "slippage_model": "EXECUTABLE_BBO_PRICES",
        "atr": {"basis": "DECLARED_SIGNAL_ATR_HOLD_CONSTANT", "atr_pct": atr,
            "observed_ts": observed, "available_at_ts": now,
            "provenance": "CYCLE_3M_INDICATOR_ORIGINAL_CAPTURE_TIMESTAMP"},
        "coverage_policy": {"sampling_interval_sec": sampling_interval_sec,
            "first_sample_offset_sec": 1, "required_horizon_sec": required_horizon_sec},
        "qualification_eligible": False,
        "limitations": ["ZERO_ADDITIONAL_LATENCY", "FIXED_INITIAL_NOTIONAL_FEE_SCENARIO",
            "SIGNAL_ATR_HELD_CONSTANT", "FUNDING_REQUIRES_TERMINAL_EVIDENCE"],
    }
    return {"status": "DECLARED_DIAGNOSTIC", "reasons": [], "declaration": declaration}
