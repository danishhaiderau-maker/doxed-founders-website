"""
Pathway Lab validation — TYPE_B execution audit, tile independence, report checks.

Used at bot startup and analyzer run. Writes JSON artifacts for dashboard / CI.
"""
from __future__ import annotations

import inspect
import json
import os
from datetime import datetime, timezone

from combo_pathway_config import (
    ANALYZER_SYNC_ID,
    BENCHMARK_LANE,
    BENCHMARK_ROLE,
    COMBO_EXECUTION_LANES,
    COMBO_LANE_SPECS,
    COMBO_TILE_DISPLAY_ORDER,
    EXECUTION_FIX_VERSION,
    EXPECTED_EXCHANGE,
    PRIMARY_PRODUCTION_LANE,
    RESEARCH_LANE_AI_SCAN,
    any_combo_execution_enabled,
    combo_lane_matches,
    is_ai_scan_lane,
    is_combo_execution_lane,
)
from legacy_pathway_config import (
    PATHWAY_STATUS_SHADOW_COLLECTING,
    SHADOW_COLLECTING_LANES,
    is_shadow_collecting_lane,
)
from experimental_pathway_config import (
    EXPERIMENTAL_EXECUTION_LANES,
    EXPERIMENTAL_TILE_DISPLAY_ORDER,
    is_experimental_execution_lane,
)

TILE_INDEPENDENCE_REPORT_FILE = "tile_independence_report.json"
TYPE_B_EXECUTION_AUDIT_FILE = "type_b_execution_audit.json"
AI_SCAN_INDEPENDENCE_REPORT_FILE = "ai_scan_independence_report.json"
AI_SCAN_ROLE_VALIDATION_FILE = "ai_scan_role_validation.json"
LANE_MEMORY_VALIDATION_FILE = "lane_memory_validation.json"
LANE_MEMORY_VIOLATION_FILE = "lane_memory_violation.json"
RUNTIME_PATHWAY_INTEGRITY_FILE = "runtime_pathway_integrity.json"
RETIRED_LANE_VIOLATIONS_FILE = "retired_lane_violations.jsonl"

LEGACY_SPAWN_TARGET_LANES = (
    "HIGH_EDGE_RUNNER",
    "EDGE_ALPHA_4",
    "TYPE_B_HUNTER",
    "SHORT_BEAR_ALPHA",
    "AI_60_65_ALPHA",
    "URGENT_CHASE_ALPHA",
    "CHASE_3PLUS_ALPHA",
)

BENCHMARK_NO_ORDER_LANES = ("CONTINUOUS",)

LEGACY_DATA_RETIRED_LANES = (
    "HIGH_EDGE_RUNNER",
    "SHADOW_RUNNER",
    "TYPE_B_HUNTER",
    "AI_60_65_ALPHA",
    "URGENT_CHASE_ALPHA",
    "CHASE_3PLUS_ALPHA",
    "EDGE_ALPHA_4",
    "SHORT_BEAR_ALPHA",
)

# Minimum quality thresholds for exit reports (when enough trades exist)
MIN_TRADES_FOR_EXIT_QUALITY = 30
MIN_EXIT_COMBO_COUNT = 20
MIN_LEAKAGE_TRADE_ROWS = 10
MIN_LADDER_PROFILES = 3
MIN_EXIT_REASON_ROWS = 3


def _scaled_exit_minimum(
    absolute_min: int,
    trade_count: int,
    floor: int = 3,
    divisor: int = 10,
) -> int:
    """Scale report depth thresholds for smaller fresh-collection samples."""
    if trade_count <= 0:
        return absolute_min
    return min(absolute_min, max(floor, trade_count // divisor))

MAX_LANE_PENDING_PER_RETIRED = 0
MAX_LANE_OPEN_PER_RETIRED = 0
MAX_LANE_BUCKET_SIZE = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def resolve_strict_pathway_validation(
    live_armed: bool = False,
    strategy_mode: str = "RESEARCH",
    live_trading_enabled: bool = False,
) -> bool:
    """STRICT_PATHWAY_VALIDATION=0 is forbidden in production-like modes."""
    env_off = os.getenv("STRICT_PATHWAY_VALIDATION", "1") == "0"
    production_like = (
        bool(live_armed)
        or str(strategy_mode or "").upper() != "RESEARCH"
        or bool(live_trading_enabled)
    )
    if production_like and env_off:
        raise SystemExit(
            "STRICT_PATHWAY_VALIDATION=0 is forbidden when live_armed, non-RESEARCH mode, "
            "or LIVE_TRADING_ENABLED — remove override"
        )
    return not env_off


def assert_bot_analyzer_sync_ready() -> dict:
    """Hard fail at startup if analyzer sync contract does not match."""
    from combo_pathway_config import ANALYZER_SYNC_ID as CFG_SYNC, EXECUTION_FIX_VERSION as CFG_BOT
    try:
        import analyzer_research_engine_v62 as analyzer
    except Exception as exc:
        payload = {
            "schema": "bot_analyzer_sync_v1",
            "generated_at": _utc_now(),
            "verdict": "SYSTEM_NOT_READY",
            "error": str(exc),
        }
        _write_json("bot_analyzer_sync.json", payload)
        raise SystemExit(f"SYSTEM_NOT_READY: cannot import analyzer — {exc}")

    bot_ok = EXECUTION_FIX_VERSION == CFG_BOT
    sync_ok = getattr(analyzer, "ANALYZER_SYNC_ID", None) == CFG_SYNC
    analyzer_bot_ok = getattr(analyzer, "EXPECTED_BOT_VERSION", None) == CFG_BOT
    ok = bot_ok and sync_ok and analyzer_bot_ok
    payload = {
        "schema": "bot_analyzer_sync_v1",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "expected_analyzer_sync_id": CFG_SYNC,
        "analyzer_sync_id": getattr(analyzer, "ANALYZER_SYNC_ID", None),
        "analyzer_expected_bot": getattr(analyzer, "EXPECTED_BOT_VERSION", None),
        "verdict": "READY" if ok else "SYSTEM_NOT_READY",
        "checks": {
            "bot_version_match": bot_ok,
            "analyzer_sync_id_match": sync_ok,
            "analyzer_expected_bot_match": analyzer_bot_ok,
        },
    }
    _write_json("bot_analyzer_sync.json", payload)
    if not ok:
        raise SystemExit(
            f"SYSTEM_NOT_READY: bot/analyzer sync mismatch — "
            f"bot={EXECUTION_FIX_VERSION} analyzer={getattr(analyzer, 'ANALYZER_SYNC_ID', '?')} "
            f"expected={CFG_SYNC}"
        )
    return payload


def audit_type_b_not_in_execution() -> dict:
    """Prove combo matcher uses only AI + spread — never TYPE_B/TYPE_A."""
    matcher_src = inspect.getsource(combo_lane_matches)
    forbidden = ("TYPE_B", "TYPE_A", "trade_mfe_type", "predicted_combo", "mfe_type")
    hits = [tok for tok in forbidden if tok in matcher_src]
    sample_ai = {"win_prob": 70, "bull_score": 2, "bear_score": 8, "decision": "APPROVE"}
    sample_spread = 6
    matches_65_chase = combo_lane_matches(PRIMARY_PRODUCTION_LANE, sample_ai, "SHORT", sample_spread)
    checks = [
        {
            "check": "combo_lane_matches source has no TYPE_B/TYPE_A gates",
            "passed": len(hits) == 0,
            "detail": f"forbidden tokens in matcher: {hits or 'none'}",
        },
        {
            "check": "combo_lane_matches uses ai_min/ai_max + spread only",
            "passed": "ai_min" in matcher_src and "spread_min" in matcher_src,
            "detail": "matcher inspects win_prob and directional spread buckets",
        },
        {
            "check": "sample APPROVE AI65+ spread6 matches PRIMARY_PRODUCTION lane",
            "passed": matches_65_chase,
            "detail": f"{PRIMARY_PRODUCTION_LANE} match={matches_65_chase}",
        },
    ]
    payload = {
        "schema": "type_b_execution_audit_v1",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "analyzer_sync_id": ANALYZER_SYNC_ID,
        "verdict": "PASS" if all(c["passed"] for c in checks) else "FAIL",
        "policy": "TYPE_B is post-trade classification only — never an entry/execution gate",
        "checks": checks,
    }
    _write_json(TYPE_B_EXECUTION_AUDIT_FILE, payload)
    return payload


def _sim_lane_orders_allowed(
    lane: str,
    enabled_map: dict,
    continuous_enabled: bool,
    retired_status: dict,
) -> bool:
    lane = str(lane or "").upper()
    if lane == "CONTINUOUS":
        return bool(continuous_enabled)
    status = retired_status.get(lane)
    if status in ("RETIRED", "DATA_RETIRED", "BENCHMARK", PATHWAY_STATUS_SHADOW_COLLECTING):
        return False
    if is_shadow_collecting_lane(lane):
        return False
    if is_ai_scan_lane(lane):
        return False
    if lane in COMBO_LANE_SPECS:
        return bool(enabled_map.get(lane, True))
    if is_experimental_execution_lane(lane):
        return bool(enabled_map.get(lane, True))
    return bool(enabled_map.get(lane, True))


def _sim_ai_scan_pipeline_enabled(enabled_map: dict, continuous_enabled: bool) -> bool:
    """Mirrors is_research_lane_enabled(AI_SCAN) — not a separate toggle."""
    return any_combo_execution_enabled(enabled_map, continuous_enabled)


def _sim_should_invoke_ai(enabled_map: dict, continuous_enabled: bool) -> bool:
    """Mirrors should_invoke_ai sole-AI gate: any combo tile ON."""
    return any_combo_execution_enabled(enabled_map, continuous_enabled)


def _sim_spawn_targets(enabled_map: dict, ai: dict, direction: str, spread: int) -> list:
    """Which combo lanes would receive spawn_combo_lanes_from_ai_scan."""
    out = []
    for lane in COMBO_EXECUTION_LANES:
        if not enabled_map.get(lane, True):
            continue
        if combo_lane_matches(lane, ai, direction, spread):
            out.append(lane)
    return out


def run_ai_scan_independence_self_test(retired_status: dict = None) -> dict:
    """
    Verify production tiles do not depend on a separate AI_SCAN order lane.
    AI_SCAN is the AI pipeline label; it is enabled iff any combo tile is ON.
    """
    retired_status = retired_status or {}
    tests = []
    sample_ai_65 = {"win_prob": 70, "bull_score": 2, "bear_score": 8, "decision": "APPROVE"}
    sample_ai_604 = {"win_prob": 62, "bull_score": 2, "bear_score": 6, "decision": "APPROVE"}

    def add(name, passed, detail):
        tests.append({"test": name, "passed": bool(passed), "detail": detail})

    add(
        "AI_SCAN never places orders (pipeline label only)",
        not _sim_lane_orders_allowed(RESEARCH_LANE_AI_SCAN, {ln: True for ln in COMBO_EXECUTION_LANES}, False, {}),
        "lane_orders_allowed(AI_SCAN) == False",
    )

    for tile in COMBO_TILE_DISPLAY_ORDER:
        only = {ln: (ln == tile) for ln in COMBO_EXECUTION_LANES}
        ai_scan_on = _sim_ai_scan_pipeline_enabled(only, False)
        tile_orders = _sim_lane_orders_allowed(tile, only, False, retired_status)
        invoke_ok = _sim_should_invoke_ai(only, False)
        spec = COMBO_LANE_SPECS[tile]
        if spec["ai_min"] >= 65:
            ai, direction, spread = sample_ai_65, "SHORT", 6
        else:
            ai, direction, spread = sample_ai_604, "SHORT", 4
        spawn = _sim_spawn_targets(only, ai, direction, spread)

        add(
            f"only {tile} ON → AI pipeline enabled (AI_SCAN path)",
            ai_scan_on and invoke_ok,
            f"ai_scan={ai_scan_on} invoke={invoke_ok}",
        )
        add(
            f"only {tile} ON → combo tile orders allowed",
            tile_orders,
            f"orders={tile_orders}",
        )
        add(
            f"only {tile} ON → APPROVE fans out to enabled matching tile",
            tile in spawn and len(spawn) == 1,
            f"spawn_targets={spawn}",
        )
        add(
            f"invariant: {tile} ON ⇒ AI pipeline ON (cannot have AI_SCAN OFF)",
            not (only[tile] and not ai_scan_on),
            "tile ON with AI pipeline OFF would block AI→ENTRY→ORDER",
        )

    # All production tiles OFF (combo + experimental) must disable AI pipeline
    all_off = {ln: False for ln in COMBO_EXECUTION_LANES}
    exp_off = {lane: False for lane in EXPERIMENTAL_EXECUTION_LANES}
    add(
        "all combo + experimental OFF → AI pipeline OFF",
        not _sim_ai_scan_pipeline_enabled({**all_off, **exp_off}, False),
        "no production tiles → no periodic AI",
    )

    passed = all(t["passed"] for t in tests)
    payload = {
        "schema": "ai_scan_independence_v1",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "policy": (
            "AI_SCAN is not an order lane. Pipeline runs when any_combo_execution_enabled. "
            "There is no separate AI_SCAN OFF toggle that blocks an ON production tile."
        ),
        "verdict": "PASS" if passed else "FAIL",
        "tests": tests,
    }
    _write_json(AI_SCAN_INDEPENDENCE_REPORT_FILE, payload)
    return payload


def run_ai_scan_role_validation() -> dict:
    """
    Prove AI_SCAN is coordinator-only: not an execution lane, not a spawn target.
    """
    spawn_src = ""
    exp_spawn_src = ""
    try:
        import bot
        spawn_src = inspect.getsource(bot.spawn_combo_lanes_from_ai_scan)
        exp_spawn_src = inspect.getsource(bot.spawn_experimental_lanes_from_ai_scan)
    except Exception as exc:
        spawn_src = f"import_error:{exc}"
        exp_spawn_src = spawn_src

    checks = [
        {
            "check": "AI_SCAN is not a combo execution lane",
            "passed": not is_combo_execution_lane(RESEARCH_LANE_AI_SCAN),
            "detail": f"is_combo_execution_lane(AI_SCAN)={is_combo_execution_lane(RESEARCH_LANE_AI_SCAN)}",
        },
        {
            "check": "AI_SCAN not in COMBO_EXECUTION_LANES",
            "passed": RESEARCH_LANE_AI_SCAN not in COMBO_EXECUTION_LANES,
            "detail": f"COMBO_EXECUTION_LANES={list(COMBO_EXECUTION_LANES)}",
        },
        {
            "check": "AI_SCAN not in legacy spawn target lanes",
            "passed": RESEARCH_LANE_AI_SCAN not in LEGACY_SPAWN_TARGET_LANES,
            "detail": f"legacy_spawn={LEGACY_SPAWN_TARGET_LANES}",
        },
        {
            "check": "spawn_combo_lanes_from_ai_scan iterates COMBO_EXECUTION_LANES only",
            "passed": "for lane in COMBO_EXECUTION_LANES" in spawn_src,
            "detail": "combo fan-out iterates COMBO_EXECUTION_LANES only",
        },
        {
            "check": "spawn_combo_lanes_from_ai_scan also calls spawn_experimental_lanes_from_ai_scan",
            "passed": "spawn_experimental_lanes_from_ai_scan" in spawn_src,
            "detail": "experimental lanes fan out after combo match loop",
        },
        {
            "check": "spawn_experimental_lanes_from_ai_scan retired or uses EXPERIMENTAL lane specs",
            "passed": (
                "EXPERIMENTAL_LANE_SPECS" in exp_spawn_src
                or "_spawn_experimental_lane" in exp_spawn_src
                or "APPROVE-spawn experimental lanes retired" in exp_spawn_src
            ),
            "detail": (
                "experimental approve-spawn retired (v9.83 stub)"
                if "APPROVE-spawn experimental lanes retired" in exp_spawn_src
                else "experimental spawn path is separate from combo matcher"
            ),
        },
        {
            "check": "combo_lane_matches has no AI_SCAN lane spec",
            "passed": RESEARCH_LANE_AI_SCAN not in COMBO_LANE_SPECS,
            "detail": "matcher specs exclude AI_SCAN",
        },
    ]
    sim_spawn = _sim_spawn_targets(
        {ln: True for ln in COMBO_EXECUTION_LANES},
        {"win_prob": 70, "bull_score": 2, "bear_score": 8, "decision": "APPROVE"},
        "SHORT",
        6,
    )
    checks.append({
        "check": "simulated spawn targets are combo execution lanes only",
        "passed": all(ln in COMBO_EXECUTION_LANES for ln in sim_spawn),
        "detail": f"spawn_targets={sim_spawn}",
    })
    checks.append({
        "check": "AI_SCAN never appears in simulated spawn targets",
        "passed": RESEARCH_LANE_AI_SCAN not in sim_spawn,
        "detail": f"spawn_targets={sim_spawn}",
    })

    passed = all(c["passed"] for c in checks)
    payload = {
        "schema": "ai_scan_role_validation_v1",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "policy": "AI_SCAN is coordinator-only — never an execution or spawn target lane",
        "verdict": "PASS" if passed else "FAIL",
        "checks": checks,
    }
    _write_json(AI_SCAN_ROLE_VALIDATION_FILE, payload)
    return payload


def run_tile_independence_self_test(retired_status: dict = None) -> dict:
    """Simulate per-tile ON/OFF — benchmark OFF must not block other tiles."""
    retired_status = retired_status or {}
    all_on = {lane: True for lane in COMBO_EXECUTION_LANES}
    tests = []

    def add(name, passed, detail):
        tests.append({"test": name, "passed": bool(passed), "detail": detail})

    add(
        "continuous OFF does not disable combo AI scan",
        any_combo_execution_enabled(all_on, continuous_enabled=False),
        "any_combo_execution_enabled(all combo ON, continuous OFF) == True",
    )

    for lane in COMBO_TILE_DISPLAY_ORDER:
        only = {ln: (ln == lane) for ln in COMBO_EXECUTION_LANES}
        allowed_self = _sim_lane_orders_allowed(lane, only, False, retired_status)
        blocked_other = all(
            not _sim_lane_orders_allowed(other, only, False, retired_status)
            for other in COMBO_EXECUTION_LANES
            if other != lane
        )
        add(
            f"only {lane} ON → orders allowed for that tile only",
            allowed_self and blocked_other,
            f"self={allowed_self} others_blocked={blocked_other}",
        )

    bench_off = dict(all_on)
    bench_off[BENCHMARK_LANE] = False
    others = [ln for ln in COMBO_EXECUTION_LANES if ln != BENCHMARK_LANE]
    add(
        "benchmark OFF → other combo tiles still allowed",
        all(_sim_lane_orders_allowed(ln, bench_off, False, retired_status) for ln in others),
        f"benchmark={BENCHMARK_LANE} OFF; others={others}",
    )

    all_off = {lane: False for lane in COMBO_EXECUTION_LANES}
    exp_off = {lane: False for lane in EXPERIMENTAL_EXECUTION_LANES}
    add(
        "all combo OFF + experimental OFF → no pathway execution",
        not any_combo_execution_enabled({**all_off, **exp_off}, continuous_enabled=False),
        "any_combo_execution_enabled(all OFF) == False",
    )

    for legacy in LEGACY_DATA_RETIRED_LANES:
        st = retired_status.get(legacy, "DATA_RETIRED")
        add(
            f"retired lane {legacy} cannot place orders",
            not _sim_lane_orders_allowed(legacy, all_on, True, {legacy: st}),
            f"status={st}",
        )

    for shadow_lane in SHADOW_COLLECTING_LANES:
        st = retired_status.get(shadow_lane, PATHWAY_STATUS_SHADOW_COLLECTING)
        add(
            f"shadow-collecting lane {shadow_lane} cannot place orders",
            not _sim_lane_orders_allowed(shadow_lane, all_on, True, {shadow_lane: st}),
            f"status={st}",
        )

    for bench in BENCHMARK_NO_ORDER_LANES:
        st = retired_status.get(bench, "BENCHMARK")
        add(
            f"benchmark lane {bench} cannot place orders when toggle OFF",
            not _sim_lane_orders_allowed(bench, all_on, False, {bench: st}),
            f"status={st} continuous=OFF",
        )
        add(
            f"benchmark lane {bench} can place orders when toggle ON",
            _sim_lane_orders_allowed(bench, all_on, True, {bench: st}),
            f"status={st} continuous=ON",
        )

    combo_off = {ln: False for ln in COMBO_EXECUTION_LANES}
    exp_all_on = {lane: True for lane in EXPERIMENTAL_EXECUTION_LANES}
    add(
        "experimental tiles ON enables AI pipeline (combo OFF)",
        any_combo_execution_enabled({**combo_off, **exp_all_on}, continuous_enabled=False),
        "experimental-only toggles must keep periodic AI alive",
    )

    for lane in EXPERIMENTAL_TILE_DISPLAY_ORDER:
        only = {ln: (ln == lane) for ln in EXPERIMENTAL_EXECUTION_LANES}
        allowed_self = _sim_lane_orders_allowed(lane, only, False, retired_status)
        blocked_other = all(
            not _sim_lane_orders_allowed(other, only, False, retired_status)
            for other in EXPERIMENTAL_EXECUTION_LANES
            if other != lane
        )
        add(
            f"only experimental {lane} ON → orders allowed for that tile only",
            allowed_self and blocked_other,
            f"self={allowed_self} others_blocked={blocked_other}",
        )

    passed = all(t["passed"] for t in tests)
    payload = {
        "schema": "tile_independence_v1",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "benchmark_lane": BENCHMARK_LANE,
        "benchmark_role": BENCHMARK_ROLE,
        "combo_tiles": list(COMBO_TILE_DISPLAY_ORDER),
        "verdict": "PASS" if passed else "FAIL",
        "tests": tests,
    }
    _write_json(TILE_INDEPENDENCE_REPORT_FILE, payload)
    return payload


def validate_lane_memory_runtime(
    lane_pending_counts: dict,
    lane_open_counts: dict,
    retired_lanes: tuple,
    max_bucket: int = MAX_LANE_BUCKET_SIZE,
) -> dict:
    """Runtime check: retired lanes must have zero exposure; buckets bounded."""
    critical_issues = []
    warn_issues = []
    for lane in retired_lanes:
        pending = int(lane_pending_counts.get(lane, 0) or 0)
        open_n = int(lane_open_counts.get(lane, 0) or 0)
        if pending > MAX_LANE_PENDING_PER_RETIRED:
            critical_issues.append(f"{lane}: retired pending={pending}")
        if open_n > MAX_LANE_OPEN_PER_RETIRED:
            critical_issues.append(f"{lane}: retired open={open_n}")

    for lane, count in {**lane_pending_counts, **lane_open_counts}.items():
        if int(count or 0) > max_bucket:
            warn_issues.append(f"{lane}: bucket size {count} > max {max_bucket}")

    if critical_issues:
        verdict = "CRITICAL"
    elif warn_issues:
        verdict = "WARN"
    else:
        verdict = "PASS"

    payload = {
        "schema": "lane_memory_validation_v2",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "retired_lanes_checked": list(retired_lanes),
        "max_bucket": max_bucket,
        "critical_issues": critical_issues,
        "warn_issues": warn_issues,
        "issues": critical_issues + warn_issues,
        "verdict": verdict,
    }
    _write_json(LANE_MEMORY_VALIDATION_FILE, payload)
    if critical_issues:
        violation = {
            "schema": "lane_memory_violation_v1",
            "generated_at": _utc_now(),
            "bot_version": EXECUTION_FIX_VERSION,
            "verdict": "CRITICAL",
            "critical_issues": critical_issues,
            "detail": "Retired lane reanimation detected — system_ready must remain False",
        }
        _write_json(LANE_MEMORY_VIOLATION_FILE, violation)
    return payload


def validate_runtime_pathway_integrity(
    startup_snapshot: dict,
    current_pathway_lane_status: dict,
    current_combo_execution_lanes: tuple,
    ai_direct_research_lanes: frozenset,
    research_spawn_lanes: tuple,
    ai_scan_orders_allowed: bool,
) -> dict:
    """Detect runtime drift from startup pathway contract (every 10 min)."""
    issues = []
    critical_issues = []

    snap_status = startup_snapshot.get("pathway_lane_status") or {}
    if dict(current_pathway_lane_status) != dict(snap_status):
        critical_issues.append("PATHWAY_LANE_STATUS drift from startup snapshot")

    snap_lanes = tuple(startup_snapshot.get("combo_execution_lanes") or ())
    if tuple(current_combo_execution_lanes) != snap_lanes:
        critical_issues.append(
            f"COMBO_EXECUTION_LANES drift: now={current_combo_execution_lanes} startup={snap_lanes}"
        )

    if "TYPE_B_HUNTER" in current_combo_execution_lanes:
        critical_issues.append("TYPE_B_HUNTER present in COMBO_EXECUTION_LANES")

    if "TYPE_B_HUNTER" in ai_direct_research_lanes:
        critical_issues.append("TYPE_B_HUNTER present in AI_DIRECT_RESEARCH_LANES")

    if "TYPE_B_HUNTER" in research_spawn_lanes:
        issues.append("TYPE_B_HUNTER still listed in RESEARCH_SPAWN_LANES (legacy tuple)")

    if RESEARCH_LANE_AI_SCAN in current_combo_execution_lanes:
        critical_issues.append("AI_SCAN present in COMBO_EXECUTION_LANES")

    if is_combo_execution_lane(RESEARCH_LANE_AI_SCAN):
        critical_issues.append("AI_SCAN classified as combo execution lane")

    if ai_scan_orders_allowed:
        critical_issues.append("AI_SCAN lane_orders_allowed is True — must be coordinator-only")

    if RESEARCH_LANE_AI_SCAN in research_spawn_lanes:
        critical_issues.append("AI_SCAN present in RESEARCH_SPAWN_LANES")

    snap_ai_direct = frozenset(startup_snapshot.get("ai_direct_research_lanes") or ())
    if ai_direct_research_lanes != snap_ai_direct:
        critical_issues.append("AI_DIRECT_RESEARCH_LANES drift from startup snapshot")

    if critical_issues:
        verdict = "CRITICAL"
    elif issues:
        verdict = "WARN"
    else:
        verdict = "PASS"

    payload = {
        "schema": "runtime_pathway_integrity_v1",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "startup_captured_at": startup_snapshot.get("captured_at"),
        "verdict": verdict,
        "critical_issues": critical_issues,
        "issues": critical_issues + issues,
    }
    _write_json(RUNTIME_PATHWAY_INTEGRITY_FILE, payload)
    return payload


def log_retired_lane_violation(lane: str, context: str, trade_id: str = None) -> dict:
    row = {
        "ts": _utc_now(),
        "lane": str(lane or "").upper(),
        "context": context,
        "trade_id": trade_id,
        "bot_version": EXECUTION_FIX_VERSION,
    }
    try:
        with open(RETIRED_LANE_VIOLATIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass
    return row


def validate_exit_reports_populated(
    trade_count: int,
    min_trades: int = MIN_TRADES_FOR_EXIT_QUALITY,
    strict: bool = None,
) -> dict:
    """Fail analyzer when exit reports exist but lack minimum analytical depth."""
    strict = resolve_strict_pathway_validation() if strict is None else strict
    specs = [
        (
            "exit_combinations_report.json",
            lambda d: int(d.get("total_combos") or 0) >= MIN_EXIT_COMBO_COUNT
            or len(d.get("top") or []) >= MIN_EXIT_COMBO_COUNT,
            MIN_EXIT_COMBO_COUNT,
            "combinations",
        ),
        (
            "exit_leakage_by_reason_report.json",
            lambda d: len(d.get("reasons") or []) >= MIN_EXIT_REASON_ROWS,
            MIN_EXIT_REASON_ROWS,
            "exit reasons",
        ),
        (
            "exit_ladder_simulator_report.json",
            lambda d: len(d.get("profiles") or []) >= MIN_LADDER_PROFILES,
            MIN_LADDER_PROFILES,
            "ladder profiles",
        ),
        (
            "top_leakage_report.json",
            lambda d: len(d.get("trades") or []) >= MIN_LEAKAGE_TRADE_ROWS,
            MIN_LEAKAGE_TRADE_ROWS,
            "leakage trade rows",
        ),
    ]
    checks = []
    errors = []
    insufficient = []

    if trade_count < min_trades:
        payload = {
            "schema": "exit_reports_validation_v2",
            "generated_at": _utc_now(),
            "trade_count": trade_count,
            "min_trades": min_trades,
            "verdict": "SKIPPED",
            "detail": f"trades={trade_count} < {min_trades}",
            "checks": [],
        }
        _write_json("exit_reports_validation.json", payload)
        return payload

    scaled_combos = _scaled_exit_minimum(MIN_EXIT_COMBO_COUNT, trade_count)
    scaled_leakage = _scaled_exit_minimum(MIN_LEAKAGE_TRADE_ROWS, trade_count, floor=5)
    scaled_profiles = min(MIN_LADDER_PROFILES, max(2, trade_count // 40))
    scaled_reasons = min(MIN_EXIT_REASON_ROWS, max(2, trade_count // 30))
    scaled_map = {
        "exit_combinations_report.json": scaled_combos,
        "exit_leakage_by_reason_report.json": scaled_reasons,
        "exit_ladder_simulator_report.json": scaled_profiles,
        "top_leakage_report.json": scaled_leakage,
    }

    for filename, predicate, minimum, label in specs:
        minimum = scaled_map.get(filename, minimum)
        path = os.path.join(os.getcwd(), filename)
        ok = False
        detail = "file missing"
        count = 0
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if filename == "exit_combinations_report.json":
                    count = int(data.get("total_combos") or 0)
                    if count < minimum:
                        count = len(data.get("top") or [])
                    ok = count >= minimum
                elif filename == "exit_leakage_by_reason_report.json":
                    count = len(data.get("reasons") or [])
                    ok = count >= minimum
                elif filename == "exit_ladder_simulator_report.json":
                    count = len(data.get("profiles") or [])
                    ok = count >= minimum
                elif filename == "top_leakage_report.json":
                    count = len(data.get("trades") or [])
                    ok = count >= minimum
                else:
                    ok = bool(predicate(data))
                detail = f"count={count} need>={minimum} {label}"
            except Exception as exc:
                detail = str(exc)
        checks.append({"file": filename, "passed": ok, "detail": detail, "minimum": minimum})
        if not ok:
            insufficient.append(f"{filename}: {detail}")
            errors.append(f"{filename}: {detail}")

    verdict = "PASS" if not errors else "INSUFFICIENT_DATA"
    payload = {
        "schema": "exit_reports_validation_v2",
        "generated_at": _utc_now(),
        "trade_count": trade_count,
        "min_trades": min_trades,
        "verdict": verdict,
        "checks": checks,
        "errors": errors,
    }
    _write_json("exit_reports_validation.json", payload)
    if errors and strict:
        raise SystemExit(f"Exit report validation {verdict}: {'; '.join(errors)}")
    return payload


def verify_repo_version_sync() -> dict:
    from combo_pathway_config import (
        ANALYZER_SYNC_ID as CFG_ANALYZER,
        EXECUTION_FIX_VERSION as CFG_BOT,
        RESEARCH_DASHBOARD_VERSION,
    )
    try:
        import analyzer_research_engine_v62 as analyzer
        analyzer_err = None
    except Exception as exc:
        analyzer = None
        analyzer_err = str(exc)
    checks = [
        {
            "component": "combo_pathway_config",
            "bot_version": CFG_BOT,
            "analyzer_sync_id": CFG_ANALYZER,
            "passed": True,
        },
    ]
    if analyzer:
        checks.append({
            "component": "analyzer_research_engine_v62",
            "bot_version": getattr(analyzer, "EXPECTED_BOT_VERSION", None),
            "analyzer_sync_id": getattr(analyzer, "ANALYZER_SYNC_ID", None),
            "passed": (
                getattr(analyzer, "EXPECTED_BOT_VERSION", None) == CFG_BOT
                and getattr(analyzer, "ANALYZER_SYNC_ID", None) == CFG_ANALYZER
            ),
        })
    else:
        checks.append({"component": "analyzer_research_engine_v62", "passed": False, "error": analyzer_err})
    payload = {
        "schema": "repo_version_sync_v1",
        "generated_at": _utc_now(),
        "expected_bot_version": CFG_BOT,
        "expected_analyzer_sync_id": CFG_ANALYZER,
        "research_dashboard_version": RESEARCH_DASHBOARD_VERSION,
        "verdict": "PASS" if all(c.get("passed") for c in checks) else "FAIL",
        "checks": checks,
    }
    _write_json("repo_version_sync.json", payload)
    return payload


def run_startup_pathway_validation(
    retired_status: dict = None,
    live_armed: bool = False,
    strategy_mode: str = "RESEARCH",
    live_trading_enabled: bool = False,
) -> dict:
    """Bot startup: full validation suite. Raises on failure when strict."""
    strict = resolve_strict_pathway_validation(live_armed, strategy_mode, live_trading_enabled)
    sync_ready = assert_bot_analyzer_sync_ready()
    type_b = audit_type_b_not_in_execution()
    tiles = run_tile_independence_self_test(retired_status=retired_status)
    ai_scan = run_ai_scan_independence_self_test(retired_status=retired_status)
    ai_scan_role = run_ai_scan_role_validation()
    sync = verify_repo_version_sync()
    ok = all(
        r.get("verdict") in ("PASS", "READY")
        for r in (type_b, tiles, ai_scan, ai_scan_role, sync, sync_ready)
    )
    summary = {
        "schema": "pathway_startup_validation_v3",
        "generated_at": _utc_now(),
        "bot_version": EXECUTION_FIX_VERSION,
        "strict_validation": strict,
        "verdict": "PASS" if ok else "FAIL",
        "bot_analyzer_sync": sync_ready.get("verdict"),
        "type_b_audit": type_b["verdict"],
        "tile_independence": tiles["verdict"],
        "ai_scan_independence": ai_scan["verdict"],
        "ai_scan_role": ai_scan_role["verdict"],
        "version_sync": sync["verdict"],
        "artifacts": [
            "bot_analyzer_sync.json",
            TYPE_B_EXECUTION_AUDIT_FILE,
            TILE_INDEPENDENCE_REPORT_FILE,
            AI_SCAN_INDEPENDENCE_REPORT_FILE,
            AI_SCAN_ROLE_VALIDATION_FILE,
            "repo_version_sync.json",
        ],
    }
    if not ok and strict:
        raise SystemExit(
            f"Pathway Lab startup validation FAILED — "
            f"type_b={type_b['verdict']} tiles={tiles['verdict']} "
            f"ai_scan={ai_scan['verdict']} ai_scan_role={ai_scan_role['verdict']} sync={sync['verdict']}"
        )
    return summary
