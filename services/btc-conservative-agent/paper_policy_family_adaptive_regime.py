"""Adaptive regime entry over the ATR Trail exit (paper only, relay-ineligible).

The signal-time entry decision is the generic ``adaptive_regime_entry``
primitive bound to this tile's lane and policy identity: CALM maker within one
tick, NORMAL or fast-move taker with a bounded protection cap, EXTREME or a
wide initial stop stands aside, and raw AI NO_TRADE or a score gap below the
minimum never trades. The exit is ATR Trail SL 1.5 / arm 0.75 / trail 1 with
no profit-lock ladder.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from adaptive_regime_entry import (
    ACTION_MAKER,
    ACTION_STAND_ASIDE,
    ACTION_TAKER,
    DECISION_SCHEMA,
    AdaptiveRegimeEntry,
    price_tick,
    realized_vol_bps,
)
from family_policy_common import PolicySpec, account_risk_quantity as _size, chase_due as _chase, dashboard_policy as _dashboard, entry_fields as _entry, exit_action as _exit, exit_config as _config
from combo_pathway_config import COMBO_LANE_SPECS

LANE = "FAMILY_ADAPTIVE_REGIME"
POLICY_ID = COMBO_LANE_SPECS[LANE]["raw_policy_id"]
POLICY_SIGNATURE = COMBO_LANE_SPECS[LANE]["policy_signature"]
ENTRY = COMBO_LANE_SPECS[LANE]["entry_policy"]
ADAPTIVE_ENTRY = True

SPEC = PolicySpec(policy_id=POLICY_ID, lane=LANE, label="Adaptive regime entry + ATR trail", family="ATR_TRAIL", entry_offset_pct=0.0, chase_windows=(), chase_interval_sec=0, chase_step=0.0, entry_ttl_sec=int(ENTRY["maker_ttl_sec"]), initial_stop_atr_k=1.5, trail_activation_atr_k=0.75, trail_atr_k=1.0)
CHASE_STEP = SPEC.chase_step

ADAPTIVE = AdaptiveRegimeEntry(
    lane=LANE, policy_id=POLICY_ID, policy_signature=POLICY_SIGNATURE,
    entry=ENTRY, initial_stop_atr_k=float(SPEC.initial_stop_atr_k),
)
RV_WINDOW_MIN = ADAPTIVE.rv_window_min
FAST_LOOKBACK_MIN = ADAPTIVE.fast_lookback_min
FAST_SIGMA_WINDOW_MIN = ADAPTIVE.fast_sigma_window_min
MIN_CLOSED_CANDLES = ADAPTIVE.min_closed_candles

__all__ = (
    "ACTION_MAKER", "ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA",
    "price_tick", "realized_vol_bps",
)


def fast_move_z(closes: Sequence[float], direction: str) -> float | None:
    return ADAPTIVE.fast_move_z(closes, direction)


def classify_regime(rv_bps: float | None) -> str:
    return ADAPTIVE.classify_regime(rv_bps)


def ai_admission_block(ai_feature: Mapping[str, Any] | None) -> str | None:
    return ADAPTIVE.ai_admission_block(ai_feature)


def decide_entry(**kwargs) -> dict[str, Any]:
    return ADAPTIVE.decide_entry(**kwargs)


def decision_is_executable(decision: Mapping[str, Any] | None, direction: str) -> bool:
    return ADAPTIVE.decision_is_executable(decision, direction)


def adaptive_entry_fields(direction, reference_price, decision):
    return ADAPTIVE.entry_fields(
        _entry(SPEC, direction, reference_price), direction, decision, SPEC.entry_ttl_sec,
    )


def entry_fields(direction, reference_price):
    return adaptive_entry_fields(direction, reference_price, None)


def chase_due(*, created_ts, last_chase_ts, now):
    return _chase(SPEC, created_ts=created_ts, last_chase_ts=last_chase_ts, now=now)


def account_risk_quantity(*, equity_usd, entry_price, atr_abs, leverage=100.0):
    return _size(SPEC, equity_usd=equity_usd, entry_price=entry_price, atr_abs=atr_abs, leverage=leverage)


def exit_action(**kwargs):
    return _exit(SPEC, **kwargs)


def exit_config(analyzer_sync_id):
    return _config(SPEC, analyzer_sync_id)


def dashboard_policy():
    payload = _dashboard(SPEC)
    payload["filter_chips"] = ADAPTIVE.filter_chips(SPEC.hard_stop_margin_pct)
    payload["entry"].update(ADAPTIVE.dashboard_entry())
    return payload
