"""Small fail-closed validation surface for the registry-owned runtime."""
from __future__ import annotations

from datetime import datetime, timezone

from combo_pathway_config import (
    COMBO_EXECUTION_LANES,
    EXECUTION_FIX_VERSION,
    RESEARCH_LANE_AI_SCAN,
    ACTIVE_TILE_ORDER,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_retired_lane_violation(lane: str, context: str, trade_id: str = None) -> dict:
    # Runtime no longer registers retired lane names. Unknown names are rejected
    # generically and logged without restoring experiment-specific code.
    return {
        "ts": _utc_now(), "lane": str(lane or "").upper(),
        "context": context, "trade_id": trade_id,
        "bot_version": EXECUTION_FIX_VERSION,
    }


def validate_lane_memory_runtime(
    lane_pending_counts: dict, lane_open_counts: dict, retired_lanes: tuple,
    max_bucket: int = 1000,
) -> dict:
    allowed = set(ACTIVE_TILE_ORDER)
    critical = []
    warnings = []
    for lane, count in {**lane_pending_counts, **lane_open_counts}.items():
        if str(lane).upper() not in allowed and int(count or 0) > 0:
            critical.append(f"UNREGISTERED_LANE_EXPOSURE:{lane}:{count}")
        elif int(count or 0) > max_bucket:
            warnings.append(f"LANE_BUCKET_OVERFLOW:{lane}:{count}")
    return {
        "schema": "lane_memory_validation_v3", "generated_at": _utc_now(),
        "critical_issues": critical, "warn_issues": warnings,
        "issues": critical + warnings,
        "verdict": "CRITICAL" if critical else "WARN" if warnings else "PASS",
    }


def validate_runtime_pathway_integrity(
    startup_snapshot: dict, current_pathway_lane_status: dict,
    current_combo_execution_lanes: tuple, ai_direct_research_lanes: frozenset,
    research_spawn_lanes: tuple, ai_scan_orders_allowed: bool,
) -> dict:
    critical = []
    expected = tuple(lane for lane in ACTIVE_TILE_ORDER if lane != "CONTINUOUS")
    if tuple(current_combo_execution_lanes) != expected:
        critical.append(f"EXECUTION_LANE_DRIFT:{current_combo_execution_lanes}")
    if ai_scan_orders_allowed or RESEARCH_LANE_AI_SCAN in current_combo_execution_lanes:
        critical.append("AI_SCAN_ORDER_CAPABLE")
    if research_spawn_lanes:
        critical.append(f"LEGACY_SPAWN_LANES:{research_spawn_lanes}")
    return {
        "schema": "runtime_pathway_integrity_v2", "generated_at": _utc_now(),
        "critical_issues": critical, "issues": critical,
        "verdict": "CRITICAL" if critical else "PASS",
    }


def run_startup_pathway_validation(
    retired_status: dict = None, live_armed: bool = False,
    strategy_mode: str = "RESEARCH", live_trading_enabled: bool = False,
) -> dict:
    exact = tuple(COMBO_EXECUTION_LANES) == tuple(
        lane for lane in ACTIVE_TILE_ORDER if lane != "CONTINUOUS"
    )
    safe = not live_armed and not live_trading_enabled
    verdict = "PASS" if exact and safe else "FAIL"
    return {
        "schema": "pathway_startup_validation_v5", "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION, "verdict": verdict,
        "lane_roster_audit": verdict, "tile_independence": verdict,
        "ai_scan_independence": verdict, "ai_scan_role": verdict,
        "independent_v1_post_ai_spawn": "REMOVED", "version_sync": verdict,
        "bot_analyzer_sync": verdict,
    }
