import ast
from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import paper_policy_offset029 as offset_policy


ROOT = Path(__file__).parent
BOT = ROOT / "bot.py"
ANALYZER = ROOT / "analyzer_research_engine_v62.py"
LANE = "RESEARCH_LANE_OFFSET_029_ATR_TP_25"


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node)


def _load_function(path: Path, name: str, env: dict):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), env)
    return env[name]


def test_paused_offset_is_cancelled_not_shadow_replayed():
    process = _function_source(BOT, "process_signal")
    spawn = _function_source(BOT, "_spawn_combo_lane")
    lab = _function_source(BOT, "_spawn_lab_combo_shadow")
    cancel_pending = _function_source(BOT, "circuit_breaker_cancel_pending")

    assert f"event_lane == {LANE}" in process
    assert "paused_shadow_mode = False" in process
    assert "_manual_pause_block_entry" in process
    assert "SPAWN_CANCELLED" in spawn
    assert "PAPER_LANE_TOGGLE_OFF_NO_SHADOW" in spawn
    assert f"upper() == {LANE}" in lab
    assert "start_replay_buffer" in lab  # guard must precede the generic replay engine
    assert lab.index("refused forbidden LAB/shadow replay") < lab.rindex("start_replay_buffer")
    assert 'record_expired=True' in cancel_pending
    assert 'expire_signal=True' in cancel_pending
    assert "start_replay_buffer" not in cancel_pending


def test_offset_never_writes_type_b_or_shadow_ledgers():
    child = _function_source(BOT, "_record_type_b_research_v2_child")
    finalize = _function_source(BOT, "finalize_shadow_lane_collecting")
    shadow = _function_source(BOT, "log_shadow_outcome_jsonl")

    assert LANE in child and child.index("return") < child.index("append_type_b_research_v2_event")
    assert LANE in finalize and finalize.index("return") < finalize.index("_safe_append_jsonl")
    assert LANE in shadow and shadow.index("return") < shadow.index("_safe_append_jsonl")


def test_offset_outcome_cannot_use_scenario_c_profit_lock():
    begin = _function_source(BOT, "begin_approve_research")
    exits = _function_source(BOT, "_apply_position_exits")
    offset_exit = _function_source(BOT, "_apply_offset_029_atr_exit")

    assert LANE in begin and begin.index("return") < begin.index("start_replay_buffer")
    assert exits.index("_apply_offset_029_atr_exit") < exits.index("_check_phase_margin_stop")
    assert "PROFIT_LOCK_LADDER" not in offset_exit
    assert "offset029_policy.exit_decision" in offset_exit


def test_analyzer_excludes_preserved_policy_mismatch_rows():
    shadow_loader = _function_source(ANALYZER, "_load_shadow_lane_outcome_df")
    type_b_report = _function_source(ANALYZER, "type_b_research_v2_report")

    assert "OFFSET_029_ATR_TP_25" in shadow_loader
    assert '== "OFFSET_029_ATR_TP_25"' in shadow_loader
    assert "policy_mismatch_rows_excluded" in shadow_loader
    assert "policy_mismatch_events_excluded" in type_b_report
    assert "OFFSET029_PAPER_ONLY_FORBIDS_TYPE_B_SHADOW_EVENTS" in type_b_report


def test_shadow_loader_behavior_keeps_evidence_but_excludes_offset_from_ranking():
    rows = [
        {"research_lane": "OFFSET_029_ATR_TP_25", "net_pnl_usd": 99.0},
        {"research_lane": "CONTINUOUS", "net_pnl_usd": 1.0},
    ]
    fn = _load_function(
        ANALYZER,
        "_load_shadow_lane_outcome_df",
        {
            "pd": pd,
            "SHADOW_LANE_OUTCOME_FILE": "unused",
            "_load_jsonl_rows": lambda _path: rows,
            "_session_start_ts": lambda _session: None,
            "filter_df_since_session": lambda df, *_args, **_kwargs: df,
        },
    )
    ranked = fn()
    assert ranked["research_lane"].tolist() == ["CONTINUOUS"]
    assert ranked.attrs["policy_mismatch_rows_excluded"] == 1
    assert rows[0]["net_pnl_usd"] == 99.0  # immutable source evidence untouched


def test_offset_dashboard_gate_places_now_despite_global_chase_and_spread_filters():
    fn = _load_function(
        BOT,
        "evaluate_dashboard_execution_gate",
        {
            "state_lock": nullcontext(),
            "state": {"ai_enabled": True, "leverage": 100},
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "MAX_RESEARCH_LEVERAGE": 100,
            "lane_orders_allowed": lambda _lane: True,
            "ensure_signal_capacity": lambda: True,
            # These global experiment gates must not be consulted for Offset.
            "_signal_spread_gate_blocked": lambda *_args: (_ for _ in ()).throw(AssertionError("spread gate leaked")),
            "dashboard_ai_band_blocks": lambda *_args: (_ for _ in ()).throw(AssertionError("AI band leaked")),
            "get_chase_execution_buckets": lambda: {},
        },
    )
    allowed, reason, defer = fn(
        {"research_lane": offset_policy.LANE, "structural_entry_valid": True},
        {"decision": "APPROVE"},
        stage="promote",
    )
    assert (allowed, reason, defer) == (True, "OK_OFFSET029_REGISTERED_POLICY", False)


def test_offset_resolver_preserves_exact_anchor_without_smart_reanchor():
    fn = _load_function(
        BOT,
        "_resolve_submit_limit_price",
        {
            "RESEARCH_LANE_OFFSET_029_ATR_TP_25": offset_policy.LANE,
            "offset029_policy": offset_policy,
            "resolve_entry_limit_price": lambda signal: (signal["planned_limit_price"], "AI_DIRECT"),
            "smart_submit_enabled": lambda: (_ for _ in ()).throw(AssertionError("smart submit leaked")),
        },
    )
    signal = offset_policy.entry_fields("LONG", 70_000)
    signal["research_lane"] = offset_policy.LANE
    planned, mode, meta, raw = fn(signal)
    assert planned == raw == 69_797.0
    assert mode == "AI_DIRECT"
    assert meta["reanchored"] is False
    assert meta["policy_id"] == offset_policy.POLICY_ID


def test_offset_order_path_disables_generic_chase_and_relay_side_effects():
    place = _function_source(BOT, "_place_simulated_limit_order")
    touch_grid = _function_source(BOT, "_arm_chase_offset_touch_grid")
    assert "dashboard_exact_chase_managed = not registered_offset_policy" in place
    assert "if not registered_offset_policy:\n        _relay_mirror" in place
    assert "if not registered_offset_policy:\n        _maybe_bitfinex_limit_entry" in place
    assert "if not registered_offset_policy:\n        _log_shadow_vs_live_entry" in place
    assert "funnel_on_order(signal, order)" in place
    assert "lane_register_pending_order(order)" in place
    assert LANE in touch_grid and touch_grid.index("return") < touch_grid.index("arm_touch_grid_rows")


def test_offset_post_approve_path_bypasses_legacy_strategy_gates():
    process = _function_source(BOT, "process_signal")
    assert "registered_offset_policy" in process
    for legacy_gate in (
        "evaluate_profitability_entry_gates",
        "evaluate_golden_stack_filter",
        "evaluate_sr_direction_filter",
        "evaluate_entry_location_filter",
        "evaluate_entry_quality_filter",
        "evaluate_evidence_entry_filter",
        "resolve_entry_margin_usdt",
    ):
        call = process.index(legacy_gate)
        preceding = process[max(0, call - 180):call]
        assert "registered_offset_policy" in preceding, legacy_gate
    assert "if not registered_offset_policy:\n                log_golden_stack_rejection" in process
    assert "if is_combo_execution_lane(research_lane) and not registered_offset_policy" in process
