"""Shared, deterministic paper-policy mechanics for registry-owned family tiles.

This module is execution infrastructure, not an active tile.  A tile becomes
active only through ``ACTIVE_TILE_REGISTRY`` and a thin immutable
``paper_policy_*.py`` binding.  Keeping the mechanics here makes retirement
physical: removing a binding and its registry row leaves no dormant policy
branch in the bot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PolicySpec:
    policy_id: str
    lane: str
    label: str
    family: str
    entry_offset_pct: float
    chase_windows: tuple[int, ...]
    chase_interval_sec: int
    chase_step: float
    entry_ttl_sec: int = 1800
    max_duration_sec: int = 7200
    initial_stop_atr_k: float | None = None
    hard_stop_margin_pct: float = 30.0
    atr_target_k: float | None = None
    chandelier_atr_k: float | None = None
    trail_activation_atr_k: float | None = None
    trail_atr_k: float | None = None
    partial_targets: tuple[tuple[float, float], ...] = ()
    mfe_giveback_fraction: float | None = None
    margin_cap_usd: float = 0.25
    account_risk_pct: float = 0.5
    relay_eligible: bool = False

    @property
    def initial_rest_sec(self) -> int:
        return min(self.chase_windows, default=0) * 300

    @property
    def chase_end_sec(self) -> int:
        return (max(self.chase_windows, default=-1) + 1) * 300


@dataclass(frozen=True)
class ExitAction:
    reason: str
    close_fraction: float
    trigger_price: float
    stop_price: float | None
    remaining_fraction: float
    peak_price: float
    partial_key: str | None = None


def _direction_sign(direction: str) -> int:
    direction = str(direction or "").upper()
    return 1 if direction == "LONG" else -1 if direction == "SHORT" else 0


def _atr_abs(entry: float, atr_abs: float, atr_pct: float) -> float:
    value = float(atr_abs or 0)
    if value <= 0 and float(entry or 0) > 0 and float(atr_pct or 0) > 0:
        value = float(entry) * float(atr_pct) / 100.0
    return max(0.0, value)


def _level(entry: float, sign: int, atr: float, multiple: float) -> float:
    return float(entry) + sign * float(atr) * float(multiple)


def _favorable_hit(sign: int, price: float, level: float) -> bool:
    return float(price) >= level if sign > 0 else float(price) <= level


def _adverse_hit(sign: int, price: float, level: float) -> bool:
    return float(price) <= level if sign > 0 else float(price) >= level


def _margin_return_pct(entry: float, sign: int, price: float, leverage: float) -> float:
    return sign * (float(price) - float(entry)) / float(entry) * float(leverage) * 100.0


def initial_limit(spec: PolicySpec, direction: str, reference_price: float) -> float | None:
    sign = _direction_sign(direction)
    price = float(reference_price or 0)
    if not sign or price <= 0:
        return None
    factor = 1.0 - spec.entry_offset_pct / 100.0 if sign > 0 else 1.0 + spec.entry_offset_pct / 100.0
    return round(price * factor, 2)


def entry_fields(spec: PolicySpec, direction: str, reference_price: float) -> dict[str, Any]:
    limit_price = initial_limit(spec, direction, reference_price)
    return {
        "entry_limit_policy": spec.policy_id,
        "raw_policy_id": spec.policy_id,
        "policy_id": spec.policy_id,
        "research_lane": spec.lane,
        "entry_path": f"FAMILY_{spec.family}",
        "entry_reason": f"DETERMINISTIC_{spec.entry_offset_pct:g}PCT_OFFSET",
        "deterministic_entry_offset_pct": spec.entry_offset_pct / 100.0,
        "deterministic_initial_limit": limit_price,
        "ai_direct_limit": limit_price,
        "planned_limit_price": limit_price,
        "structural_entry_valid": limit_price is not None,
        "await_micro_confirm": False,
        "await_5m_confirm": False,
        "micro_structure_confirmed": True,
        "order_placed": False,
        "paper_only": True,
        "relay_eligible": bool(spec.relay_eligible),
        "chase_windows": list(spec.chase_windows),
        "chase_start_sec": spec.initial_rest_sec,
        "chase_end_sec": spec.chase_end_sec,
        "chase_interval_sec": spec.chase_interval_sec,
        "chase_remaining_gap_step": spec.chase_step,
        "entry_ttl_sec": spec.entry_ttl_sec,
        "initial_stop_atr_k": spec.initial_stop_atr_k,
        "hard_stop_margin_pct": spec.hard_stop_margin_pct,
        "atr_tp_multiple": spec.atr_target_k,
        "path_end_sec": spec.max_duration_sec,
        "margin_cap_usd": spec.margin_cap_usd,
        "account_risk_pct": spec.account_risk_pct,
    }


def chase_due(spec: PolicySpec, *, created_ts: float, last_chase_ts: float, now: float) -> bool:
    age = float(now) - float(created_ts or 0)
    bucket = int(age // 300)
    return (
        bucket in spec.chase_windows
        and float(now) - float(last_chase_ts or created_ts or 0) >= spec.chase_interval_sec
    )


def marketable_quote_at_limit(*, direction: str, limit_price: float,
                              bid: float, ask: float) -> bool:
    sign = _direction_sign(direction)
    limit_price = float(limit_price or 0)
    if not sign or limit_price <= 0:
        return False
    return (
        float(ask or 0) > 0 and float(ask) <= limit_price
        if sign > 0
        else float(bid or 0) > 0 and float(bid) >= limit_price
    )


def account_risk_quantity(spec: PolicySpec, *, equity_usd: float, entry_price: float,
                          atr_abs: float, leverage: float = 100.0) -> dict[str, Any]:
    risk_budget = max(0.0, float(equity_usd or 0)) * spec.account_risk_pct / 100.0
    if spec.initial_stop_atr_k is not None:
        risk_distance = max(float(atr_abs or 0) * float(spec.initial_stop_atr_k), 1e-12)
    else:
        # Convert the physical margin-return hard stop back to an underlying
        # price distance. This keeps no-ATR-stop research families non-zero
        # while preserving both account-risk and margin-cap bounds.
        risk_distance = max(
            float(entry_price or 0)
            * abs(float(spec.hard_stop_margin_pct))
            / (max(float(leverage or 0), 1.0) * 100.0),
            1e-12,
        )
    risk_qty = risk_budget / risk_distance
    margin_qty = spec.margin_cap_usd * max(float(leverage or 0), 1.0) / max(float(entry_price or 0), 1.0)
    quantity = min(risk_qty, margin_qty)
    return {
        "quantity": quantity,
        "risk_budget_usd": risk_budget,
        "margin_cap_usd": spec.margin_cap_usd,
        "capped_by": "MARGIN" if margin_qty <= risk_qty else "ACCOUNT_RISK",
    }


def exit_action(spec: PolicySpec, *, entry: float, direction: str, price: float,
                atr_abs: float = 0.0, atr_pct: float = 0.0,
                age_sec: float = 0.0, leverage: float = 100.0,
                remaining_fraction: float = 1.0,
                completed_partials: tuple[str, ...] | list[str] = (),
                peak_price: float | None = None) -> ExitAction | None:
    entry = float(entry or 0); price = float(price or 0)
    sign = _direction_sign(direction)
    atr = _atr_abs(entry, atr_abs, atr_pct)
    remaining = max(0.0, min(1.0, float(remaining_fraction or 0)))
    if entry <= 0 or price <= 0 or atr <= 0 or not sign or remaining <= 0:
        return None
    previous_peak = float(peak_price if peak_price is not None else entry)
    peak = max(previous_peak, price) if sign > 0 else min(previous_peak, price)
    hard_hit = _margin_return_pct(entry, sign, price, leverage) <= -abs(spec.hard_stop_margin_pct)
    stop_price = None
    dynamic_stop_active = False
    if spec.initial_stop_atr_k is not None:
        stop_price = _level(entry, sign, atr, -float(spec.initial_stop_atr_k))

    favorable_atr = sign * (peak - entry) / atr
    if spec.chandelier_atr_k is not None and favorable_atr >= float(spec.trail_activation_atr_k or 0):
        candidate = peak - sign * atr * float(spec.chandelier_atr_k)
        stop_price = candidate if stop_price is None else (max(stop_price, candidate) if sign > 0 else min(stop_price, candidate))
        dynamic_stop_active = True
    if spec.trail_atr_k is not None and favorable_atr >= float(spec.trail_activation_atr_k or 0):
        candidate = peak - sign * atr * float(spec.trail_atr_k)
        stop_price = candidate if stop_price is None else (max(stop_price, candidate) if sign > 0 else min(stop_price, candidate))
        dynamic_stop_active = True
    if spec.mfe_giveback_fraction is not None and favorable_atr > 0:
        candidate = entry + (peak - entry) * (1.0 - float(spec.mfe_giveback_fraction))
        stop_price = candidate if stop_price is None else (max(stop_price, candidate) if sign > 0 else min(stop_price, candidate))
        dynamic_stop_active = True

    if hard_hit:
        return ExitAction("PHYSICAL_HARD_STOP_30PCT", remaining, price, stop_price, 0.0, peak)
    if stop_price is not None and _adverse_hit(sign, price, stop_price):
        reason = "PROFIT_PROTECTION_STOP" if dynamic_stop_active else "INITIAL_ATR_STOP"
        return ExitAction(reason, remaining, stop_price, stop_price, 0.0, peak)

    completed = set(completed_partials or ())
    for index, (trigger_atr, fraction) in enumerate(spec.partial_targets):
        key = f"partial_{index}_{trigger_atr:g}atr"
        target = _level(entry, sign, atr, trigger_atr)
        if key not in completed and _favorable_hit(sign, price, target):
            close = min(float(fraction), remaining)
            return ExitAction(
                f"PARTIAL_TP_{trigger_atr:g}_ATR", close, target, stop_price,
                remaining - close, peak, partial_key=key,
            )

    if spec.atr_target_k is not None:
        target = _level(entry, sign, atr, float(spec.atr_target_k))
        if _favorable_hit(sign, price, target):
            return ExitAction("ATR_TP", remaining, target, stop_price, 0.0, peak)
    if float(age_sec or 0) >= spec.max_duration_sec:
        return ExitAction("PATH_END_120M", remaining, price, stop_price, 0.0, peak)
    return None


def exit_config(spec: PolicySpec, analyzer_sync_id: str) -> dict[str, Any]:
    return {
        "policy_snapshot_schema": "exit_policy_v1",
        "policy_source": "btc-conservative-agent",
        "policy_version": spec.policy_id,
        "raw_policy_id": spec.policy_id,
        "analyzer_sync_id": analyzer_sync_id,
        "exit_profile_id": spec.policy_id.split("|", 1)[-1],
        "family": spec.family,
        "atr_source": "FILL_TIME_3M_ATR14",
        "initial_stop_atr_k": spec.initial_stop_atr_k,
        "hard_stop_margin_pct": spec.hard_stop_margin_pct,
        "atr_tp_multiple": spec.atr_target_k,
        "chandelier_atr_k": spec.chandelier_atr_k,
        "trail_activation_atr_k": spec.trail_activation_atr_k,
        "trailing_stop_atr_k": spec.trail_atr_k,
        "partial_take_profits": [list(row) for row in spec.partial_targets],
        "mfe_giveback_fraction": spec.mfe_giveback_fraction,
        "path_end_sec": spec.max_duration_sec,
        "partial_reduction_required": bool(spec.partial_targets),
    }


def dashboard_policy(spec: PolicySpec) -> dict[str, Any]:
    chips = [
        "PAPER ONLY", f"Offset {spec.entry_offset_pct:g}%",
        f"Chase {','.join(map(str, spec.chase_windows))}",
        f"Every {spec.chase_interval_sec}s", f"Move {spec.chase_step * 100:g}% gap",
        f"Hard stop {spec.hard_stop_margin_pct:g}%", "120m cap",
    ]
    return {
        "filter_chips": chips,
        "entry": {
            "trigger": "Shared three-minute AI direction; independent tile decision",
            "entry_path": f"FAMILY_{spec.family}",
            "fill_path": "CONSERVATIVE_BBO_DEPTH_PAPER_LIMIT",
            "execution": "Independent paper lifecycle; relay fail-closed",
            "chase_detail": (
                f"Buckets {spec.chase_windows}; {spec.chase_step * 100:g}% remaining-gap "
                f"reprice every {spec.chase_interval_sec}s"
            ),
        },
        "exit": {
            "profile": spec.family,
            "initial_stop_atr_k": spec.initial_stop_atr_k,
            "hard_stop_margin_pct": spec.hard_stop_margin_pct,
            "atr_target_k": spec.atr_target_k,
            "chandelier_atr_k": spec.chandelier_atr_k,
            "trail_activation_atr_k": spec.trail_activation_atr_k,
            "trail_atr_k": spec.trail_atr_k,
            "partials": [list(row) for row in spec.partial_targets],
            "mfe_giveback_fraction": spec.mfe_giveback_fraction,
            "fixed_time_exit": "120m",
        },
        "strategy_detail": [
            f"Raw policy: {spec.policy_id}",
            "One shared AI call; independent identity, order, position and ledger",
            "Side-correct BBO/depth fill required; last-price touch is insufficient",
            "Tile ON is paper eligibility only; relay remains fail-closed",
        ],
    }
