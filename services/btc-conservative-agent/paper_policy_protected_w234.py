"""Frozen paper-only policy for the descriptive Protected W234 chase candidate."""
from __future__ import annotations

POLICY_ID = "OFFSET_0.28_CHASE_w234_s10_i180|ATR_TP_2.5_SCENARIO_C_ATR_SL_2.5"
LANE = "PROTECTED_W234_SCENARIO_C"
ENTRY_OFFSET = 0.0028
CHASE_STEP = 0.10
CHASE_INTERVAL_SEC = 180
CHASE_START_SEC = 600
CHASE_END_SEC = 1500
ENTRY_TTL_SEC = 1800
ATR_TP_MULTIPLE = 2.5
ATR_STOP_MULTIPLE = 2.5
PATH_END_SEC = 7200


def entry_fields(direction: str, signal_price: float) -> dict:
    direction = str(direction or "").upper()
    price = float(signal_price or 0)
    limit_price = price * (1 - ENTRY_OFFSET if direction == "LONG" else 1 + ENTRY_OFFSET)
    valid = price > 0 and direction in ("LONG", "SHORT")
    return {
        "raw_policy_id": POLICY_ID, "policy_id": POLICY_ID,
        "entry_limit_policy": "OFFSET_0.28_CHASE_w234_s10_i180",
        "entry_path": "AI_DIRECT_CHASE", "entry_reason": "PROTECTED_W234_CHASE",
        "pullback_pct": ENTRY_OFFSET, "deterministic_entry_offset_pct": ENTRY_OFFSET,
        "ai_direct_limit": round(limit_price, 2) if valid else None,
        "planned_limit_price": round(limit_price, 2) if valid else None,
        "deterministic_initial_limit": round(limit_price, 2) if valid else None,
        "structural_entry_valid": valid, "paper_only": True, "relay_eligible": False,
        "chase_start_sec": CHASE_START_SEC, "chase_end_sec": CHASE_END_SEC,
        "chase_interval_sec": CHASE_INTERVAL_SEC,
        "chase_remaining_gap_step": CHASE_STEP, "entry_ttl_sec": ENTRY_TTL_SEC,
        "atr_tp_multiple": ATR_TP_MULTIPLE, "atr_stop_multiple": ATR_STOP_MULTIPLE,
        "path_end_sec": PATH_END_SEC,
    }


def chase_due(*, created_ts: float, last_chase_ts: float, now: float) -> bool:
    age = float(now) - float(created_ts or 0)
    return CHASE_START_SEC <= age < CHASE_END_SEC and float(now) - float(last_chase_ts or created_ts or 0) >= CHASE_INTERVAL_SEC


def marketable_quote_at_limit(*, direction: str, limit_price: float, bid: float, ask: float) -> bool:
    direction = str(direction or "").upper()
    if direction == "LONG":
        return float(ask or 0) > 0 and float(ask) <= float(limit_price or 0)
    if direction == "SHORT":
        return float(bid or 0) > 0 and float(bid) >= float(limit_price or 0)
    return False


def atr_distance(atr_abs: float, atr_pct: float, entry: float) -> float:
    absolute = float(atr_abs or 0)
    if absolute > 0:
        return absolute
    # Runtime atr14_pct_3m is percentage points (for example 0.42 == 0.42%).
    return float(entry or 0) * float(atr_pct or 0) / 100.0


def tp_price(entry: float, direction: str, atr_abs: float, atr_pct: float):
    distance = atr_distance(atr_abs, atr_pct, entry) * ATR_TP_MULTIPLE
    if distance <= 0:
        return None
    return float(entry) + distance if str(direction).upper() == "LONG" else float(entry) - distance


def stop_price(entry: float, direction: str, atr_abs: float, atr_pct: float):
    distance = atr_distance(atr_abs, atr_pct, entry) * ATR_STOP_MULTIPLE
    if distance <= 0:
        return None
    return float(entry) - distance if str(direction).upper() == "LONG" else float(entry) + distance


def stop_hit(price: float, stop: float, direction: str) -> bool:
    if not stop:
        return False
    return float(price) <= float(stop) if str(direction).upper() == "LONG" else float(price) >= float(stop)


def exit_config(analyzer_sync_id: str) -> dict:
    ladder = [[8, 5], [12, 10], [19, 17], [40, 28], [60, 45], [80, 60], [100, 75], [150, 120]]
    return {
        "policy_snapshot_schema": "exit_policy_v1", "policy_source": "btc-conservative-agent",
        "policy_version": POLICY_ID, "raw_policy_id": POLICY_ID,
        "analyzer_sync_id": analyzer_sync_id,
        "exit_profile_id": "ATR_TP_2.5_SCENARIO_C_ATR_SL_2.5_V1",
        "atr_source": "FILL_TIME_3M_ATR14", "atr_tp_multiple": ATR_TP_MULTIPLE,
        "atr_stop_multiple": ATR_STOP_MULTIPLE, "path_end_sec": PATH_END_SEC,
        "scenario_c_exit_profile": True, "trail_ladder": ladder,
        "ladder_first_trigger_pct": 8, "ladder_first_lock_pct": 5,
        "hard_stop_margin_pct": -30.0, "thesis_fast_exit_unreal_pct": -12.0,
        "thesis_mfe_protect_pct": 5.0, "thesis_exit_if_above_unreal_pct": 8.0,
        "thesis_min_age_sec": 300,
    }


def dashboard_policy() -> dict:
    return {
        "filter_chips": [
            "PAPER ONLY", "Offset 0.28%", "Rest 0–10m", "Chase 10–25m",
            "Every 180s", "10% remaining gap", "TTL 30m", "ATR TP 2.5×",
            "ATR stop 2.5×", "Scenario C", "Path end 120m",
        ],
        "entry": {
            "trigger": "Every shared AI_SCAN APPROVE direction; independent paper capacity",
            "entry_path": "PROTECTED_W234_CHASE",
            "fill_path": "LOCAL_PAPER_LIMIT_CONSERVATIVE_BBO",
            "ai_path": "Shared AI_SCAN direction; no second AI call",
            "chase_detail": "Rest 10m; 10% remaining-gap reprice every 180s at age 10–25m",
            "post_ai_gates": "Shared APPROVE plus normal paper health/capacity checks",
            "execution": "Independent paper lifecycle; explicitly blocked from live copy",
            "orders": "Independent signed paper lifecycle with pwch trade IDs",
        },
        "exit": {
            "profile": "ATR TP/SL 2.5× + Scenario C", "ladder": "8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75, 150→120",
            "thesis_stop_margin_pct": -12.0, "hard_stop_margin_pct": -30.0,
            "mfe_protect_margin_pct": 5.0, "thesis_pause_above_margin_pct": 8.0,
            "fixed_time_exit": "120m PATH_END at observable paper price",
        },
        "strategy_detail": [
            f"Raw policy: {POLICY_ID}",
            "Shared direction call; independent pwch trade IDs, lock, capacity, orders, positions and P&L ledger",
            "Initial limit: LONG 0.28% below / SHORT 0.28% above signal reference",
            "Rest 0–10m; every 180s from 10–25m move 10% of remaining gap; rest to 30m TTL",
            "Frozen fill-time 3m ATR(14): favorable target 2.5× and adverse stop 2.5×",
            "Scenario C ladder/thesis/hard-stop protection and 120m path end remain active",
            "Tile ON creates paper orders only; live-copy eligibility is fail-closed",
        ],
    }
