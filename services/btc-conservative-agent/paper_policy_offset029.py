"""Immutable paper-only policy math for OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5."""
from __future__ import annotations

POLICY_ID = "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5"
LANE = "OFFSET_029_ATR_TP_25"
ENTRY_OFFSET = 0.0029
CHASE_START_SEC = 600
CHASE_END_SEC = 1500
CHASE_INTERVAL_SEC = 60
CHASE_STEP = 0.25
ENTRY_TTL_SEC = 1800
ATR_TP_MULTIPLE = 2.5
PATH_END_SEC = 7200
EXIT_PROFILE_ID = "ATR_TP_2.5X_PATH_END_120M_V1"


def initial_limit(direction: str, reference_price: float) -> float | None:
    direction = str(direction or "").upper()
    price = float(reference_price or 0)
    if price <= 0 or direction not in ("LONG", "SHORT"):
        return None
    factor = 1.0 - ENTRY_OFFSET if direction == "LONG" else 1.0 + ENTRY_OFFSET
    return round(price * factor, 2)


def entry_fields(direction: str, reference_price: float) -> dict:
    limit_price = initial_limit(direction, reference_price)
    return {
        "entry_limit_policy": POLICY_ID,
        "raw_policy_id": POLICY_ID,
        "policy_id": POLICY_ID,
        "entry_path": "OFFSET_029_PATIENT_CHASE",
        "entry_reason": "DETERMINISTIC_0.29PCT_OFFSET",
        "deterministic_entry_offset_pct": ENTRY_OFFSET,
        "deterministic_initial_limit": limit_price,
        "ai_direct_limit": limit_price,
        "planned_limit_price": limit_price,
        "structural_entry_valid": limit_price is not None,
        "await_micro_confirm": False,
        "await_5m_confirm": False,
        "micro_structure_confirmed": True,
        "order_placed": False,
        "paper_only": True,
        "relay_eligible": False,
        "chase_start_sec": CHASE_START_SEC,
        "chase_end_sec": CHASE_END_SEC,
        "chase_interval_sec": CHASE_INTERVAL_SEC,
        "chase_remaining_gap_step": CHASE_STEP,
        "entry_ttl_sec": ENTRY_TTL_SEC,
        "atr_tp_multiple": ATR_TP_MULTIPLE,
        "path_end_sec": PATH_END_SEC,
    }


def chase_due(*, created_ts: float, last_chase_ts: float, now: float) -> bool:
    age = float(now) - float(created_ts or 0)
    return (
        CHASE_START_SEC <= age < CHASE_END_SEC
        and float(now) - float(last_chase_ts or created_ts or 0) >= CHASE_INTERVAL_SEC
    )


def atr_target(entry: float, direction: str, atr_abs: float = 0.0, atr_pct: float = 0.0) -> float | None:
    entry = float(entry or 0)
    atr_abs = float(atr_abs or 0)
    if atr_abs <= 0 and float(atr_pct or 0) > 0 and entry > 0:
        atr_abs = entry * float(atr_pct) / 100.0
    direction = str(direction or "").upper()
    if entry <= 0 or atr_abs <= 0 or direction not in ("LONG", "SHORT"):
        return None
    distance = ATR_TP_MULTIPLE * atr_abs
    return entry + distance if direction == "LONG" else entry - distance


def exit_decision(*, entry: float, direction: str, price: float, atr_abs: float,
                  atr_pct: float, age_sec: float) -> tuple[str | None, float | None]:
    target = atr_target(entry, direction, atr_abs, atr_pct)
    direction = str(direction or "").upper()
    if target is not None:
        hit = float(price) >= target if direction == "LONG" else float(price) <= target
        if hit:
            return "ATR_TP_2_5X", target
    if float(age_sec or 0) >= PATH_END_SEC:
        return "PATH_END_120M", target
    return None, target


def exit_config(analyzer_sync_id: str) -> dict:
    return {
        "policy_snapshot_schema": "exit_policy_v1",
        "policy_source": "btc-conservative-agent",
        "policy_version": POLICY_ID,
        "raw_policy_id": POLICY_ID,
        "analyzer_sync_id": analyzer_sync_id,
        "exit_profile_id": EXIT_PROFILE_ID,
        "exit_mode": "FROZEN_3M_ATR_TP_THEN_PATH_END",
        "atr_source": "FILL_TIME_3M_ATR14",
        "atr_tp_multiple": ATR_TP_MULTIPLE,
        "path_end_sec": PATH_END_SEC,
        "path_end_behavior": "CLOSE_AT_OBSERVABLE_PAPER_PRICE",
        "scenario_c_exit_profile": False,
        "ladder_disabled": True,
        "thesis_disabled": True,
        "hard_stop_disabled_for_policy_parity": True,
        "trail_ladder": [(1_000_000.0, 1_000_000.0)],
        "ladder_first_trigger_pct": None,
        "ladder_first_lock_pct": None,
        "hard_stop_margin_pct": None,
        "thesis_fast_exit_unreal_pct": None,
        "thesis_mfe_protect_pct": None,
        "thesis_exit_if_above_unreal_pct": None,
        "thesis_min_age_sec": None,
        "peak_never_loser_min_peak": 1_000_000.0,
        "peak_never_loser_floor": 1_000_000.0,
    }


def dashboard_policy() -> dict:
    return {
        "filter_chips": ["PAPER ONLY", "Offset 0.29%", "Rest 0–10m", "Chase 10–25m",
                         "Every 60s", "25% remaining gap", "TTL 30m", "ATR(3m) TP 2.5×",
                         "Path end 120m"],
        "entry": {
            "trigger": "Every shared AI_SCAN APPROVE direction; independent paper capacity",
            "entry_path": "OFFSET_029_PATIENT_CHASE",
            "fill_path": "LOCAL_PAPER_LIMIT",
            "ai_path": "Shared AI_SCAN direction; no second AI call",
            "chase_detail": "Rest 10m; 25% remaining-gap reprice every 60s at age 10–25m",
            "post_ai_gates": "Shared APPROVE plus normal paper health/capacity checks",
            "execution": "Local PAPER limit only; structurally ineligible for Bitfinex relay",
            "orders": "Independent local paper lifecycle; never relayed",
        },
        "exit": {
            "profile": "ATR TP 2.5× + path end", "ladder": "disabled",
            "thesis_stop_margin_pct": "disabled", "hard_stop_margin_pct": "disabled",
            "mfe_protect_margin_pct": "n/a", "thesis_pause_above_margin_pct": "n/a",
            "fixed_time_exit": "120m PATH_END at observable paper price",
        },
        "strategy_detail": [
            f"Raw policy: {POLICY_ID}",
            "One shared direction call; independent trade IDs, lock, capacity, orders, positions and P&L ledger",
            "Initial limit: LONG 0.29% below / SHORT 0.29% above signal reference",
            "Rest 0–10m; every 60s from 10–25m move 25% of remaining gap; rest to 30m TTL",
            "Exit at favorable 2.5 × frozen fill-time 3m ATR(14); otherwise 120m PATH_END mark",
            "No Scenario C ladder, thesis cut, hard stop, marketable fallback, or Bitfinex relay",
        ],
    }
