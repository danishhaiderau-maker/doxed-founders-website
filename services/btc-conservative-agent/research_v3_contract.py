"""Research Collector V3 — Safe Policy Genome contract.

This module is intentionally independent of the bot runtime.  It defines the
immutable evidence vocabulary and canonical policy identity used by collectors,
replayers, analyzers, and dashboards.  A policy is never identified by a display
label alone: its complete specification and all modeling assumptions are hashed.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

COLLECTOR_VERSION = "collector_v3.1"
CONTRACT_SCHEMA = "safe_policy_genome_contract_v1_1"
POLICY_DSL_SCHEMA = "safe_policy_dsl_v1_1"
EVIDENCE_SCHEMA = "research_evidence_v3"
EPISODE_SCHEMA = "causal_episode_v3"

OUTCOME_STATES = (
    "FULL_FILL",
    "PARTIAL_FILL",
    "NO_FILL",
    "NO_TRADE",
    "REJECTED",
    "CENSORED",
    "UNSUPPORTED",
    "DATA_ERROR",
    "REALIZED_PROFIT",
    "REALIZED_LOSS",
    "REALIZED_ZERO_PNL",
)


def normalize_lifecycle_outcome(outcome_state: Any, *, net_pnl_usd: Any = None) -> str:
    """Separate workflow status labels from contract analytical outcomes."""
    state = str(outcome_state or "CENSORED").strip().upper()
    if state == "PENDING_FILL":
        return "CENSORED"
    if state == "PAPER_REALIZED":
        try:
            pnl = float(net_pnl_usd)
        except (TypeError, ValueError):
            return "CENSORED"
        if pnl > 0:
            return "REALIZED_PROFIT"
        if pnl < 0:
            return "REALIZED_LOSS"
        return "REALIZED_ZERO_PNL"
    return state if state in OUTCOME_STATES else "DATA_ERROR"

EXECUTION_WORLDS = (
    "IDEAL_TOUCH_DIAGNOSTIC",
    "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
    "CONSERVATIVE_BBO_DEPTH_TAPE",
    "CONSERVATIVE_BBO_DEPTH_V1",
    "AUTHENTICATED_ACTUAL",
)


# A regime is a label observed at a particular causal boundary.  It is not the
# Phase-7 market taxonomy, so this helper deliberately does not project labels
# such as BULL/BEAR into a different vocabulary.  Keeping the observation
# envelope here prevents reports, candidates, and validation from each making
# a subtly different (and potentially forward-looking) interpretation.
_REGIME_UNKNOWN_LABELS = frozenset({
    "", "UNKNOWN", "UNK", "NONE", "NULL", "N/A", "NA", "NAN",
    "INF", "+INF", "-INF", "INFINITY", "+INFINITY", "-INFINITY",
})


def _finite_positive_timestamp(value: Any) -> float | None:
    """Return an epoch timestamp only when it is a finite positive scalar."""
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if math.isfinite(timestamp) and timestamp > 0 else None


def normalize_regime_observation(
    value: Any,
    *,
    signal_ts: Any,
    scalar_observed_ts: Any = None,
) -> dict[str, Any]:
    """Normalize one regime observation without inventing causal availability.

    Current collectors record ``{"value": label, "observed_ts": epoch}``.
    A legacy scalar can still be displayed descriptively, but it is not
    qualification-eligible unless its caller supplies an explicit pre-signal
    capture timestamp.  Invalid, collection-shaped, or post-signal evidence is
    represented as UNKNOWN rather than being silently bucketed or backfilled.
    """
    result: dict[str, Any] = {
        "schema": "causal_regime_observation_v1",
        "value": None,
        "observed_ts": None,
        "status": "UNKNOWN",
        "qualification_eligible": False,
        "reason": None,
    }

    envelope = isinstance(value, Mapping)
    if envelope:
        if "value" not in value:
            result["reason"] = "REGIME_OBSERVATION_VALUE_MISSING"
            return result
        raw_label = value.get("value")
        observed_raw = value.get("observed_ts")
        if observed_raw in (None, ""):
            result["reason"] = "REGIME_OBSERVATION_TIMESTAMP_MISSING"
            return result
    elif isinstance(value, str):
        raw_label = value
        observed_raw = scalar_observed_ts
    else:
        result["reason"] = "REGIME_OBSERVATION_INVALID_TYPE"
        return result

    if not isinstance(raw_label, str):
        result["reason"] = "REGIME_OBSERVATION_INVALID_LABEL"
        return result
    label = raw_label.strip().upper()
    if label in _REGIME_UNKNOWN_LABELS:
        result["reason"] = "REGIME_OBSERVATION_UNKNOWN"
        return result
    result["value"] = label

    if observed_raw in (None, ""):
        # Keep a readable historical label, but do not let an un-timestamped
        # scalar satisfy a causal regime-coverage gate.
        result["status"] = "LEGACY_SCALAR_DESCRIPTIVE_ONLY"
        result["reason"] = "REGIME_OBSERVATION_TIMESTAMP_MISSING"
        return result

    observed_ts = _finite_positive_timestamp(observed_raw)
    if observed_ts is None:
        result["value"] = None
        result["reason"] = "REGIME_OBSERVATION_TIMESTAMP_INVALID"
        return result
    result["observed_ts"] = observed_ts
    normalized_signal_ts = _finite_positive_timestamp(signal_ts)
    if normalized_signal_ts is None:
        result["value"] = None
        result["reason"] = "REGIME_SIGNAL_TIMESTAMP_INVALID"
        return result
    if observed_ts > normalized_signal_ts:
        result["value"] = None
        result["reason"] = "REGIME_OBSERVATION_POST_SIGNAL"
        return result

    result["status"] = "CAUSAL"
    result["qualification_eligible"] = True
    result["reason"] = None
    return result

LEDGER_NAMES = (
    "opportunity",
    "pre_entry_features",
    "evidence_failure",
    "decision",
    "order_intent",
    "execution",
    "market_segment",
    "lifecycle",
)

ATR_TP_MULTIPLIERS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
ATR_SL_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
THESIS_CUT_MARGIN_PCT = (-4, -6, -8, -10, -12, -15, -18, -20, -25, -30)
THESIS_WINDOWS_SEC = (60, 180, 300, 600)
HARD_STOP_MARGIN_PCT = (10, 12, 15, 18, 20, 25, 30)
TIME_STOPS_MIN = (5, 10, 15, 20, 30, 45, 60, 90, 120)
BREAK_EVEN_ARM_MFE_PCT = (2, 3, 4, 5, 6, 8, 10)
BREAK_EVEN_ARM_ATR_MULTIPLIERS = (0.75, 1.0, 1.25, 1.5, 2.0)
BREAK_EVEN_FLOOR_PCT = (0, 0.5, 1, 2)
MFE_GIVEBACK_ABS_PCT = (1, 2, 3, 4, 5, 8, 10)
MFE_GIVEBACK_FRACTION = (0.2, 0.3, 0.4, 0.5, 0.6)
CHANDELIER_ATR_MULTIPLIERS = (1.0, 1.5, 2.0, 2.5, 3.0)
ATR_TRAIL_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
TRAIL_ACTIVATION_ATR_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5)
PARTIAL_TAKE_PROFIT_PLANS = {
    "none": (),
    "secure_25_25_runner": ((1.0, 0.25), (1.5, 0.25)),
    "secure_33_runner": ((1.0, 0.33),),
    "late_25_25_runner": ((1.5, 0.25), (2.0, 0.25)),
}
CONCURRENCY_CAPS = (1, 2, 3, 5)
SIZE_SCALES = (0.25, 0.5, 0.75, 1.0)
DAILY_LOSS_KILL_PCT = (2, 3, 5, 8)
CONSECUTIVE_LOSS_PAUSE = (2, 3, 4)

LADDERS = {
    "none": (),
    "scenario_c": ((8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120)),
    "early_tight": ((2, 0.5), (3, 1), (4, 2), (5, 3), (8, 6), (12, 10)),
    "early_loose": ((4, 1), (6, 2), (10, 5), (15, 9), (25, 17)),
    "high_capture": ((4, 3), (6, 5), (10, 8), (15, 12), (25, 20), (40, 32)),
    "runner_friendly": ((5, 1), (8, 3), (12, 6), (20, 12), (40, 25), (80, 55)),
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_hash(prefix: str, value: Any, *, length: int = 32) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:length]}"


@dataclass(frozen=True)
class PolicyIdentity:
    policy_spec_hash: str
    simulator_hash: str
    fill_model_hash: str
    cost_model_hash: str
    data_snapshot_hash: str
    policy_signature: str

    def as_dict(self) -> dict[str, str]:
        return {
            "schema": "safe_policy_identity_v1",
            "policy_spec_hash": self.policy_spec_hash,
            "simulator_hash": self.simulator_hash,
            "fill_model_hash": self.fill_model_hash,
            "cost_model_hash": self.cost_model_hash,
            "data_snapshot_hash": self.data_snapshot_hash,
            "policy_signature": self.policy_signature,
        }


def build_policy_identity(
    policy_spec: Mapping[str, Any],
    *,
    simulator: Mapping[str, Any],
    fill_model: Mapping[str, Any],
    cost_model: Mapping[str, Any],
    data_snapshot: Mapping[str, Any],
) -> PolicyIdentity:
    """Hash the full experiment, including zero-fee but nonzero market costs."""
    parts = {
        "policy_spec_hash": canonical_hash("spec", policy_spec),
        "simulator_hash": canonical_hash("sim", simulator),
        "fill_model_hash": canonical_hash("fill", fill_model),
        "cost_model_hash": canonical_hash("cost", cost_model),
        "data_snapshot_hash": canonical_hash("data", data_snapshot),
    }
    return PolicyIdentity(**parts, policy_signature=canonical_hash("policy", parts))


def validate_policy_spec(spec: Mapping[str, Any]) -> list[str]:
    """Return explicit defects; absence of defects is required before replay."""
    errors: list[str] = []
    required = ("entry", "fill", "loss_protection", "profit_protection", "portfolio")
    for key in required:
        if not isinstance(spec.get(key), Mapping):
            errors.append(f"MISSING_{key.upper()}_SPEC")
    loss = spec.get("loss_protection") or {}
    if loss.get("research_negative_control") is not True and not any(
        loss.get(key) is not None for key in ("atr_stop_k", "thesis_cut_margin_pct", "hard_stop_margin_pct")
    ):
        errors.append("UNPROTECTED_POLICY")
    if loss.get("hard_stop_margin_pct") is None and loss.get("research_negative_control") is not True:
        errors.append("PHYSICAL_HARD_STOP_REQUIRED")
    execution_world = (spec.get("fill") or {}).get("execution_world")
    if execution_world not in EXECUTION_WORLDS:
        errors.append("INVALID_EXECUTION_WORLD")
    ladder = (spec.get("profit_protection") or {}).get("ladder") or []
    prior_trigger = prior_floor = float("-inf")
    for rung in ladder:
        try:
            trigger, floor = map(float, rung)
        except (TypeError, ValueError):
            errors.append("INVALID_LADDER_RUNG")
            continue
        if trigger <= prior_trigger or floor < prior_floor or floor >= trigger:
            errors.append("NON_MONOTONIC_OR_INVALID_LADDER")
        prior_trigger, prior_floor = trigger, floor
    profit = spec.get("profit_protection") or {}
    mode = str(profit.get("mode") or "ATR_TARGET")
    if mode not in {"ATR_TARGET", "ATR_TRAIL", "CHANDELIER", "MFE_GIVEBACK", "HYBRID_RUNNER"}:
        errors.append("INVALID_PROFIT_PROTECTION_MODE")
    partials = profit.get("partial_take_profits") or []
    prior_trigger = 0.0
    total_fraction = 0.0
    for rung in partials:
        try:
            trigger, fraction = map(float, rung)
        except (TypeError, ValueError):
            errors.append("INVALID_PARTIAL_TAKE_PROFIT")
            continue
        if trigger <= prior_trigger or fraction <= 0 or fraction >= 1:
            errors.append("INVALID_PARTIAL_TAKE_PROFIT")
        total_fraction += fraction
        prior_trigger = trigger
    if total_fraction >= 1:
        errors.append("PARTIAL_TAKE_PROFITS_LEAVE_NO_RUNNER")
    return sorted(set(errors))


def build_contract() -> dict[str, Any]:
    material = {
        "schema": CONTRACT_SCHEMA,
        "collector_version": COLLECTOR_VERSION,
        "evidence_schema": EVIDENCE_SCHEMA,
        "episode_schema": EPISODE_SCHEMA,
        "outcome_states": list(OUTCOME_STATES),
        "execution_worlds": list(EXECUTION_WORLDS),
        "ledgers": list(LEDGER_NAMES),
        "fees": {
            "bitfinex_trading_fee_rate": 0.0,
            "separate_non_fee_costs": ["funding", "spread", "slippage", "latency", "partial_fill", "stop_slippage"],
        },
        "protection_axes": {
            "atr_tp_k": list(ATR_TP_MULTIPLIERS),
            "atr_sl_k": list(ATR_SL_MULTIPLIERS),
            "thesis_cut_margin_pct": list(THESIS_CUT_MARGIN_PCT),
            "thesis_window_sec": list(THESIS_WINDOWS_SEC),
            "hard_stop_margin_pct": list(HARD_STOP_MARGIN_PCT),
            "time_stop_min": list(TIME_STOPS_MIN),
            "break_even_arm_mfe_pct": list(BREAK_EVEN_ARM_MFE_PCT),
            "break_even_arm_atr_k": list(BREAK_EVEN_ARM_ATR_MULTIPLIERS),
            "break_even_floor_pct": list(BREAK_EVEN_FLOOR_PCT),
            "mfe_giveback_abs_pct": list(MFE_GIVEBACK_ABS_PCT),
            "mfe_giveback_fraction": list(MFE_GIVEBACK_FRACTION),
            "chandelier_atr_k": list(CHANDELIER_ATR_MULTIPLIERS),
            "atr_trail_k": list(ATR_TRAIL_MULTIPLIERS),
            "trail_activation_atr_k": list(TRAIL_ACTIVATION_ATR_MULTIPLIERS),
            "partial_take_profit_plan": list(PARTIAL_TAKE_PROFIT_PLANS),
            "profit_protection_mode": ["ATR_TARGET", "ATR_TRAIL", "CHANDELIER", "MFE_GIVEBACK", "HYBRID_RUNNER"],
            "ladder_id": list(LADDERS),
        },
        "portfolio_axes": {
            "concurrency_cap": list(CONCURRENCY_CAPS),
            "size_scale": list(SIZE_SCALES),
            "daily_loss_kill_pct": list(DAILY_LOSS_KILL_PCT),
            "consecutive_loss_pause": list(CONSECUTIVE_LOSS_PAUSE),
        },
        "ranking": {
            "hard_reject_before_rank": [
                "INTEGRITY_DEFECT", "INCOMPLETE_PATH", "CONSERVATIVE_EXECUTION_MISSING",
                "DRAWDOWN_BUDGET_EXCEEDED", "CVAR_BUDGET_EXCEEDED", "LIQUIDATION_BUFFER_BREACH",
                "OOS_LCB_NOT_POSITIVE", "UNSTABLE_PARAMETER_NEIGHBORHOOD",
            ],
            "pareto_objectives": ["MAX_CONSERVATIVE_OOS_NET", "MAX_EXPECTANCY_LCB", "MAX_PROFIT_RETENTION", "MIN_MAX_DRAWDOWN", "MIN_CVAR95", "MIN_TIME_UNDERWATER"],
            "number_one": "Among policies passing every hard gate, choose the Pareto survivor with highest conservative sealed-OOS net profit, then lowest max drawdown and CVaR95.",
        },
        "activation": "PAPER_AND_SHADOW_ONLY_UNTIL_EXPLICIT_USER_AUTHORIZATION",
    }
    material["contract_signature"] = canonical_hash("v3-contract", material)
    return material


SAFE_POLICY_GENOME_CONTRACT = build_contract()

